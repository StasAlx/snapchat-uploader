"""
Google Drive: поиск файлов по названию и скачивание.

Поиск по базовому имени (без расширения и маркера формата):
    'CRTV-154-1_MIDEF_ED'  →  найдёт 'CRTV-154-1_9x16_MIDEF_ED.mp4'
    'CRTV-617-11'          →  найдёт 'CRTV-617-11.mp4' (без маркера формата)
    'CRTS-489-5_MIDEF_ED'  →  найдёт 'CRTS-489-5_9x16_MIDEF_ED.png'

Стратегия поиска (~1 API-запрос на файл):
    Drive API: title contains 'tok1' and title contains 'tok2' and ...
    Не требует знания структуры папок — работает с любой вложенностью.

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

_FORMAT_RE = re.compile(r"\d+x\d+", re.IGNORECASE)
_FORMAT_TOKEN_RE = re.compile(r"^\d+x\d+$", re.IGNORECASE)

_PROJECT_ROOT = Path(__file__).parent.parent


# ── Хелперы по имени файла ────────────────────────────────────────────────────

def file_basename(title: str) -> str:
    """
    Возвращает базовое имя файла: без расширения и маркера формата.
    'CRTV-154-1_9x16_MIDEF_ED.mp4' → 'CRTV-154-1_MIDEF_ED'
    'CRTV-617-11.mp4'               → 'CRTV-617-11'
    """
    stem = Path(title).stem
    cleaned = _FORMAT_RE.sub("", stem)
    cleaned = re.sub(r"_+", "_", cleaned)
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


def _tokens(s: str) -> List[str]:
    return [t for t in s.split("_") if t]


def _allowed_variant(full_name: str, base_name: str) -> bool:
    """
    Проверяет, что файл соответствует базовому имени с учётом вставки маркера формата.

    Разрешены:
      - точное совпадение stem == base_name
      - stem == base_name с ОДНИМ форматным токеном (9x16, 4x5 и т.д.),
        вставленным строго между другими токенами (не в начале и не в конце)

    Примеры:
      CRTV-154-1_MIDEF_ED           → OK (точное совпадение)
      CRTV-154-1_9x16_MIDEF_ED.mp4 → OK (9x16 вставлен между токенами)
      CRTV-154-1_MIDEF_ED_9x16.mp4 → НЕТ (в конце)
      9x16_CRTV-154-1_MIDEF_ED.mp4 → НЕТ (в начале)
    """
    stem = Path(full_name).stem
    if stem == base_name:
        return True

    base_toks = _tokens(base_name)
    var_toks = _tokens(stem)

    if len(var_toks) == len(base_toks) + 1:
        for i, tok in enumerate(var_toks):
            if _FORMAT_TOKEN_RE.match(tok):
                if i == 0 or i == len(var_toks) - 1:
                    continue
                if var_toks[:i] + var_toks[i + 1:] == base_toks:
                    return True
    return False


# ── Google Drive init ─────────────────────────────────────────────────────────

def init_gdrive(client_secrets_path: str = "client_secrets.json") -> GoogleDrive:
    """
    Инициализирует Google Drive с OAuth2. При первом запуске откроется браузер.
    Токен сохраняется в gdrive_credentials.json и используется повторно.
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


# ── Поиск по названию файла ───────────────────────────────────────────────────

def find_files_by_basenames(
    drive: GoogleDrive,
    root_folder_id: str,
    basenames: Set[str],
) -> Dict[str, object]:
    """
    Ищет файлы по базовому имени через Drive API title search.
    1 API-запрос на файл, не зависит от структуры папок.

    Алгоритм:
    - Каждый токен basename добавляется как 'title contains' условие
    - Из результатов берётся первый файл, прошедший _allowed_variant + is_uploadable
    - root_folder_id не используется в запросе (поиск по всему Drive)

    Возвращает {basename: drive_file_object}.
    """
    result: Dict[str, object] = {}

    for bn in sorted(basenames):
        tokens = _tokens(bn)
        conditions = [
            "trashed = false",
            f"mimeType != '{MIME_FOLDER}'",
            "not mimeType contains 'application/vnd.google-apps'",
        ]
        for tok in tokens:
            escaped = tok.replace("'", "\\'")
            conditions.append(f"title contains '{escaped}'")
        q = " and ".join(conditions)

        log.info("Поиск на Drive: %s ...", bn)
        try:
            files = drive.ListFile({"q": q}).GetList()
        except Exception as exc:
            log.warning("Ошибка поиска '%s': %s", bn, exc)
            continue

        matched = None
        for f in files:
            title = f["title"]
            if _allowed_variant(title, bn) and is_uploadable(title):
                matched = f
                break

        if matched:
            result[bn] = matched
            log.info("  ✓ %s  →  %s", bn, matched["title"])
        else:
            log.warning("  ✗ Не найдено: %s (всего совпадений по токенам: %d)", bn, len(files))

    missing = basenames - set(result.keys())
    if missing:
        log.warning("Итого не найдено на Drive: %s", ", ".join(sorted(missing)))

    return result


# ── Скачивание ────────────────────────────────────────────────────────────────

def download_file(drive_file, dest_path: Path) -> None:
    """Скачивает файл с Google Drive в dest_path."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    drive_file.GetContentFile(str(dest_path))
    size_kb = dest_path.stat().st_size // 1024
    log.info("Скачан: %s (%d KB)", dest_path.name, size_kb)
