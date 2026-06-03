"""
Google Drive: поиск файлов по имени и скачивание.
Структура папок: gdrive_folder_id → AUTOVideo/AUTOStatic → CRTV-xxx → файлы

Поиск по базовому имени (без расширения и маркера формата):
    'CRTV-154-1_MIDEF_ED'  →  найдёт 'CRTV-154-1_9x16_MIDEF_ED.mp4' в папке CRTV-154
    'CRTV-617-11'          →  найдёт 'CRTV-617-11.mp4' в папке CRTV-617

Стратегия поиска (быстрая, ~12 API-запросов вместо 1724):
    1. Получаем ID папок AUTOVideo и AUTOStatic (1 запрос)
    2. По каждому basename определяем нужную папку: CRTV-154-1 → CRTV-154
    3. Ищем эту папку (с учётом суффикса UPLOADED) → 1 запрос на папку
    4. Листингуем только нужные папки → 1 запрос на папку

Фильтр форматов: загружаются только 9x16 или файлы без маркера формата.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

MIME_FOLDER = "application/vnd.google-apps.folder"
log = logging.getLogger(__name__)

# Маркер формата в имени файла: 9x16, 4x5, 1x1 и т.д.
_FORMAT_RE = re.compile(r"\d+x\d+", re.IGNORECASE)

# Корневая папка проекта (tiktok-uploader/)
_PROJECT_ROOT = Path(__file__).parent.parent


# ── Хелперы по имени файла ────────────────────────────────────────────────────

def file_basename(title: str) -> str:
    """
    Возвращает базовое имя файла: без расширения и маркера формата.
    'CRTV-154-1_9x16_MIDEF_ED.mp4' → 'CRTV-154-1_MIDEF_ED'
    'CRTV-617-11.mp4'               → 'CRTV-617-11'
    """
    stem = Path(title).stem                     # убираем расширение
    cleaned = _FORMAT_RE.sub("", stem)          # убираем маркер формата
    cleaned = re.sub(r"_+", "_", cleaned)       # схлопываем двойные подчёркивания
    return cleaned.strip("_")


def file_format(title: str) -> Optional[str]:
    """
    Возвращает маркер формата в нижнем регистре, или None.
    'CRTV-154-1_9x16_MIDEF_ED.mp4' → '9x16'
    'CRTS-489-5_4x5_MIDEF_ED.jpg'  → '4x5'
    'CRTV-617-11.mp4'              → None
    """
    stem = Path(title).stem
    m = _FORMAT_RE.search(stem)
    return m.group(0).lower() if m else None


def is_uploadable(title: str) -> bool:
    """
    Возвращает True, если файл подходит для загрузки:
    - маркер формата 9x16 (вертикальное видео/фото)
    - OR маркер формата отсутствует
    """
    fmt = file_format(title)
    return fmt is None or fmt == "9x16"


def _folder_prefix(basename: str) -> str:
    """
    Извлекает имя папки из базового имени файла.
    'CRTV-154-1_MIDEF_ED' → 'CRTV-154'
    'CRTS-489-5_MIDEF_ED' → 'CRTS-489'
    'CRTV-617-11'         → 'CRTV-617'
    """
    # Берём первые два сегмента через дефис: CRTV-154
    parts = basename.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return basename


# ── Google Drive init ─────────────────────────────────────────────────────────

def init_gdrive(client_secrets_path: str = "client_secrets.json") -> GoogleDrive:
    """
    Инициализирует Google Drive с OAuth2. При первом запуске откроется браузер.
    Токен сохраняется в gdrive_credentials.json и используется повторно.

    Важно: settings передаются в конструктор GoogleAuth, а не через gauth.settings
    после создания — иначе _storages не инициализируются корректно.
    """
    secrets_abs = str(Path(client_secrets_path).resolve())
    creds_abs = str(_PROJECT_ROOT / "gdrive_credentials.json")

    gauth = GoogleAuth(settings={
        "client_config_backend": "file",
        "client_config_file": secrets_abs,
        "save_credentials": True,
        "save_credentials_backend": "file",
        "save_credentials_file": creds_abs,
        "get_refresh_token": True,
        "oauth_scope": ["https://www.googleapis.com/auth/drive"],
    })
    gauth.LocalWebserverAuth()
    return GoogleDrive(gauth)


# ── Умный поиск по папке (основной) ──────────────────────────────────────────

def find_files_by_basenames(
    drive: GoogleDrive,
    root_folder_id: str,
    basenames: Set[str],
) -> Dict[str, object]:
    """
    Ищет файлы по базовому имени (без расширения и маркера формата).
    Загружаемые форматы: только 9x16 или без маркера.

    Алгоритм (быстрый):
    - basename → prefix (CRTV-154-1 → CRTV-154) → папка на Drive
    - Листингует только нужные папки (не весь Drive)

    Возвращает {basename: drive_file_object}.
    """
    result: Dict[str, object] = {}

    # Шаг 1: найти AUTOVideo и AUTOStatic
    log.info("Получаем структуру корневой папки Drive...")
    root_items = drive.ListFile(
        {"q": f"'{root_folder_id}' in parents and trashed=false and mimeType='{MIME_FOLDER}'"}
    ).GetList()
    top_folders: Dict[str, str] = {item["title"]: item["id"] for item in root_items}

    autovideo_id = top_folders.get("AUTOVideo")
    autostatic_id = top_folders.get("AUTOStatic")
    if not autovideo_id or not autostatic_id:
        log.error("Не найдены папки AUTOVideo / AUTOStatic. Найдены: %s", list(top_folders))
        return result
    log.info("AUTOVideo: %s, AUTOStatic: %s", autovideo_id, autostatic_id)

    # Шаг 2: группируем basenames по папке-префиксу
    prefix_map: Dict[str, List[str]] = {}
    for bn in basenames:
        prefix = _folder_prefix(bn)
        prefix_map.setdefault(prefix, []).append(bn)

    # Шаг 3: для каждого префикса находим папку на Drive и ищем файлы
    for prefix, bns in prefix_map.items():
        ctype = prefix.split("-")[0].upper()   # CRTV или CRTS
        parent_id = autovideo_id if ctype == "CRTV" else autostatic_id

        # Ищем папку: может называться 'CRTV-154' или 'CRTV-154 UPLOADED'
        folder_query = (
            f"(title = '{prefix}' or title = '{prefix} UPLOADED') "
            f"and mimeType='{MIME_FOLDER}' "
            f"and '{parent_id}' in parents "
            f"and trashed=false"
        )
        log.info("Ищем папку: %s...", prefix)
        folders = drive.ListFile({"q": folder_query}).GetList()

        if not folders:
            log.warning("Папка не найдена на Drive: %s", prefix)
            continue

        folder_id = folders[0]["id"]
        folder_title = folders[0]["title"]
        log.info("  Папка найдена: %s", folder_title)

        # Листингуем файлы в папке
        files = drive.ListFile(
            {"q": f"'{folder_id}' in parents and trashed=false and mimeType!='{MIME_FOLDER}'"}
        ).GetList()
        log.info("  Файлов в папке: %d", len(files))

        for item in files:
            if not is_uploadable(item["title"]):
                log.debug("  Пропускаем (%s): %s", file_format(item["title"]), item["title"])
                continue
            bn = file_basename(item["title"])
            if bn in bns and bn not in result:
                result[bn] = item
                log.info("  ✓ %s  →  %s", bn, item["title"])

    missing = basenames - set(result.keys())
    if missing:
        log.warning("Не найдено на Drive: %s", ", ".join(sorted(missing)))

    return result


# ── Скачивание ────────────────────────────────────────────────────────────────

def download_file(drive_file, dest_path: Path) -> None:
    """Скачивает файл с Google Drive в dest_path."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    drive_file.GetContentFile(str(dest_path))
    size_kb = dest_path.stat().st_size // 1024
    log.info("Скачан: %s (%d KB)", dest_path.name, size_kb)
