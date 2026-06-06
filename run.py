"""
Snapchat Uploader — точка входа.

Использование:
    python run.py mimika_v21
    python run.py mimika_v21 --limit 3
    python run.py mimika_v21 --dry-run
"""
import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def setup_logging(funnel_name: str) -> None:
    log_dir = ROOT / "state" / funnel_name
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "last_run.log"
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%H:%M:%S"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt=datefmt,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, mode="w", encoding="utf-8"),
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapchat Ads Uploader")
    parser.add_argument("funnel", help="Имя конфига (например: mimika_v21)")
    parser.add_argument("--limit", type=int, help="Сколько файлов загрузить (для теста)")
    parser.add_argument("--dry-run", action="store_true", help="Проверить конфиг без загрузки")
    args = parser.parse_args()

    setup_logging(args.funnel)
    log = logging.getLogger(__name__)

    from core.config import load_config
    cfg = load_config(args.funnel)
    log.info("Конфиг загружен: %s", cfg.name)
    log.info("Ad Account: %s | Pixel: %s", cfg.ad_account_id, cfg.pixel_id)
    log.info("Страны: %s | Бюджет кампании: $%.0f/день | Target Cost: $%.0f", cfg.countries, cfg.campaign_budget_usd, cfg.target_cost_usd)

    if args.dry_run:
        log.info("--- DRY RUN: реальная загрузка не производится ---")
        return

    from core.uploader import run_upload
    try:
        run_upload(
            funnel_name_or_path=args.funnel,
            limit=args.limit,
        )
    except Exception as exc:
        log.error("Критическая ошибка: %s", exc, exc_info=True)
        sys.exit(1)

    # Итог в stdout
    from core.config import load_config as lc
    c = lc(args.funnel)
    print(f"\nFunnel  : {c.name}")
    print(f"Account : {c.ad_account_id}")


if __name__ == "__main__":
    main()
