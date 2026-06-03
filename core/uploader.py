"""
Основной цикл загрузки: Google Drive → Snapchat.

Логика:
- За один запуск: до batch_size (10) файлов
- Создаётся 1 кампания с CBO бюджетом
- Создаётся 2 Ad Squad: первый — первая половина файлов, второй — вторая половина
- Для каждого файла: upload_media → create_creative → create_ad
"""
from __future__ import annotations

import json
import logging
import math
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from .api import (
    create_ad, create_ad_squad, create_campaign,
    create_creative, next_midnight_account_tz, upload_media,
)
from .auth import SnapAuth
from .config import FunnelConfig, load_config
from .gdrive import download_file, find_files_by_basenames, init_gdrive

log = logging.getLogger(__name__)
ROOT = Path(__file__).parent.parent


def _creatives_file(funnel_name: str) -> Path:
    return ROOT / "creatives" / f"{funnel_name}.txt"


def _state_dir(funnel_name: str) -> Path:
    d = ROOT / "state" / funnel_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_state(path: Path) -> Dict[str, dict]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_state(path: Path, state: Dict[str, dict]) -> None:
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_creatives_list(funnel_name: str) -> List[str]:
    path = _creatives_file(funnel_name)
    if not path.exists():
        raise FileNotFoundError(f"Файл со списком креативов не найден: {path}")
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _build_names(cfg: FunnelConfig) -> str:
    suffix = datetime.now().strftime("%d%m%y_%H%M%S")
    return f"{cfg.campaign_name_template}_{suffix}"


def _split_batch(items: list) -> tuple[list, list]:
    """
    Делит список на два адсета.
    Первый получает ⌈N/2⌉, второй — ⌊N/2⌋ (при нечётном первый больше на 1).
    """
    half = math.ceil(len(items) / 2)
    return items[:half], items[half:]


def _process_batch(
    items: list,
    cfg: FunnelConfig,
    auth: SnapAuth,
    ad_squad_id: str,
    found_files: dict,
    temp_dir: Path,
    state: Dict[str, dict],
    state_path: Path,
    campaign_id: str,
    campaign_name: str,
) -> int:
    """Загружает файлы из списка в один Ad Squad. Возвращает кол-во успешных."""
    count = 0
    for basename in items:
        if basename not in found_files:
            log.warning("Нет файла на Drive: %s — пропускаем", basename)
            continue

        log.info("--- %s ---", basename)
        drive_file = found_files[basename]
        local_path = temp_dir / drive_file["title"]

        try:
            download_file(drive_file, local_path)
        except Exception as exc:
            log.error("Ошибка скачивания %s: %s — пропускаем", basename, exc)
            continue

        ad_name = Path(drive_file["title"]).stem
        try:
            media_id = upload_media(auth, cfg.ad_account_id, local_path)
            creative_id = create_creative(auth, cfg, media_id, ad_name)
            ad_id = create_ad(auth, cfg, ad_squad_id, creative_id, ad_name)
        except Exception as exc:
            log.error("Ошибка загрузки %s: %s — пропускаем", basename, exc)
            local_path.unlink(missing_ok=True)
            continue

        state[basename] = {
            "ad_id": ad_id,
            "creative_id": creative_id,
            "media_id": media_id,
            "campaign_id": campaign_id,
            "ad_squad_id": ad_squad_id,
            "campaign_name": campaign_name,
            "actual_filename": drive_file["title"],
            "uploaded_at": datetime.now().isoformat(),
        }
        _save_state(state_path, state)
        log.info("✓ %s → ad=%s", basename, ad_id)
        count += 1
        local_path.unlink(missing_ok=True)

    return count


def run_upload(
    funnel_name_or_path: str,
    client_secrets_json: str = "client_secrets.json",
    limit: Optional[int] = None,
) -> None:
    cfg = load_config(funnel_name_or_path)
    batch_size = min(limit if limit is not None else cfg.batch_size, 10)
    auth = SnapAuth()

    log.info("=== Snapchat Uploader: %s ===", cfg.name)
    log.info("Batch size: %d (макс 10)", batch_size)

    all_creatives = _load_creatives_list(cfg.name)
    log.info("Всего в списке: %d файлов", len(all_creatives))

    state_path = _state_dir(cfg.name) / "uploaded.json"
    state = _load_state(state_path)
    uploaded: Set[str] = set(state.keys())
    pending = [n for n in all_creatives if n not in uploaded]
    log.info("Уже загружено: %d, ожидают: %d", len(uploaded), len(pending))

    if not pending:
        log.info("Все креативы уже загружены.")
        return

    batch = pending[:batch_size]
    log.info("Текущий батч (%d): %s", len(batch), ", ".join(batch))

    # Поиск на Google Drive
    log.info("Поиск файлов на Google Drive...")
    drive = init_gdrive(client_secrets_json)
    found_files = find_files_by_basenames(drive, cfg.gdrive_folder_id, set(batch))

    if not found_files:
        log.error("Не найдено ни одного файла на Google Drive.")
        return

    missing = set(batch) - set(found_files.keys())
    if missing:
        log.warning("Не найдены на Drive (%d): %s", len(missing), ", ".join(sorted(missing)))

    files_to_upload = [n for n in batch if n in found_files]
    if not files_to_upload:
        return

    # Делим на 2 адсета
    group1, group2 = _split_batch(files_to_upload)
    log.info(
        "Разбивка: AdSet-1 = %d файлов, AdSet-2 = %d файлов",
        len(group1), len(group2),
    )

    # Получаем время старта по timezone аккаунта
    start_time = next_midnight_account_tz(auth, cfg.ad_account_id)

    # Создаём кампанию
    campaign_name = _build_names(cfg)
    log.info("Создаём кампанию: %s", campaign_name)
    campaign_id = create_campaign(auth, cfg, campaign_name, start_time)

    # Временная папка для файлов
    temp_dir = Path(tempfile.mkdtemp(prefix="snap_uploader_"))
    log.info("Временная папка: %s", temp_dir)
    total_uploaded = 0

    try:
        # AdSet 1
        as1_name = f"{campaign_name}_AS1"
        log.info("Создаём Ad Squad 1: %s", as1_name)
        ad_squad_id_1 = create_ad_squad(auth, cfg, campaign_id, as1_name, start_time)
        total_uploaded += _process_batch(
            group1, cfg, auth, ad_squad_id_1, found_files,
            temp_dir, state, state_path, campaign_id, campaign_name,
        )

        # AdSet 2 (создаём всегда, даже если мало файлов)
        if group2:
            as2_name = f"{campaign_name}_AS2"
            log.info("Создаём Ad Squad 2: %s", as2_name)
            ad_squad_id_2 = create_ad_squad(auth, cfg, campaign_id, as2_name, start_time)
            total_uploaded += _process_batch(
                group2, cfg, auth, ad_squad_id_2, found_files,
                temp_dir, state, state_path, campaign_id, campaign_name,
            )
        else:
            log.info("Все файлы помещены в AdSet-1 (меньше 2 файлов для AdSet-2)")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    log.info(
        "=== Готово: загружено %d/%d. Кампания: %s (ID: %s) ===",
        total_uploaded, len(files_to_upload), campaign_name, campaign_id,
    )
