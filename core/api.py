"""
Snapchat Marketing API v1:
  1. Upload Media (video/image) → media_id
  2. Create Creative (WEB_VIEW) → creative_id
  3. Create Campaign (SALES, CBO, PAUSED) → campaign_id
  4. Create Ad Squad (Target Cost, Smart Targeting) → ad_squad_id
  5. Create Ad → ad_id

Auth: Authorization: Bearer {token}
Бюджет: micro-currency ($1 = 1_000_000), бюджет на уровне кампании.
"""
from __future__ import annotations
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Tuple
from zoneinfo import ZoneInfo

import requests

from .auth import SnapAuth
from .config import FunnelConfig

BASE_URL = "https://adsapi.snapchat.com/v1"
log = logging.getLogger(__name__)

MEDIA_POLL_INTERVAL = 5
MEDIA_POLL_TIMEOUT  = 300

# Timezone рекламного аккаунта (запрашивается один раз)
_ACCOUNT_TZ: str | None = None


def get_account_timezone(auth: SnapAuth, ad_account_id: str) -> str:
    """Возвращает timezone рекламного аккаунта."""
    global _ACCOUNT_TZ
    if _ACCOUNT_TZ:
        return _ACCOUNT_TZ
    r = requests.get(
        f"{BASE_URL}/adaccounts/{ad_account_id}",
        headers=auth.headers(), timeout=10,
    )
    tz = r.json().get("adaccounts", [{}])[0].get("adaccount", {}).get("timezone", "UTC")
    _ACCOUNT_TZ = tz
    return tz


def next_midnight_account_tz(auth: SnapAuth, ad_account_id: str) -> str:
    """Полночь следующего дня в timezone аккаунта, ISO 8601 UTC."""
    tz_name = get_account_timezone(auth, ad_account_id)
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    midnight_local = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    midnight_utc = midnight_local.astimezone(timezone.utc)
    log.info("Account TZ: %s → start time UTC: %s", tz_name, midnight_utc.strftime("%Y-%m-%dT%H:%M:%SZ"))
    return midnight_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _check(resp: requests.Response, label: str) -> dict:
    if not resp.ok:
        raise RuntimeError(f"[{label}] HTTP {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    if data.get("request_status") != "SUCCESS":
        raise RuntimeError(f"[{label}] API error: {data}")
    return data


# ── Media ─────────────────────────────────────────────────────────────────────

def upload_media(auth: SnapAuth, ad_account_id: str, local_path: Path) -> str:
    """Загружает видео/изображение. Поллит до READY. Возвращает media_id."""
    is_video = local_path.suffix.lower() in (".mp4", ".mov")
    media_type = "VIDEO" if is_video else "IMAGE"

    r = requests.post(
        f"{BASE_URL}/adaccounts/{ad_account_id}/media",
        headers=auth.headers(),
        json={"media": [{"name": local_path.stem, "type": media_type, "ad_account_id": ad_account_id}]},
        timeout=15,
    )
    data = _check(r, "create_media")
    media_id = data["media"][0]["media"]["id"]

    with open(local_path, "rb") as f:
        r2 = requests.post(
            f"{BASE_URL}/media/{media_id}/upload",
            headers={"Authorization": f"Bearer {auth.token()}"},
            files={"file": (local_path.name, f, "video/mp4" if is_video else "image/jpeg")},
            timeout=300,
        )
    if not r2.ok:
        raise RuntimeError(f"[upload_media] Upload failed: {r2.status_code} {r2.text[:300]}")

    deadline = time.time() + MEDIA_POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(MEDIA_POLL_INTERVAL)
        r3 = requests.get(f"{BASE_URL}/media/{media_id}", headers=auth.headers(), timeout=15)
        status = r3.json().get("media", [{}])[0].get("media", {}).get("media_status", "")
        if status == "READY":
            log.info("Media ready: %s → %s", local_path.name, media_id)
            return media_id
        if status == "FAILED":
            raise RuntimeError(f"Media processing failed: {local_path.name}")
        log.debug("Media %s: %s...", media_id[:8], status)

    raise RuntimeError(f"Media timeout: {media_id}")


# ── Creative ──────────────────────────────────────────────────────────────────

def create_creative(auth: SnapAuth, cfg: FunnelConfig, media_id: str, name: str) -> str:
    """Создаёт WEB_VIEW creative. Возвращает creative_id."""
    payload = {
        "creatives": [{
            "name": name,
            "ad_account_id": cfg.ad_account_id,
            "type": "WEB_VIEW",
            "top_snap_media_id": media_id,
            "headline": cfg.headline,
            "brand_name": cfg.brand_name,
            "call_to_action": cfg.call_to_action,
            "profile_properties": {"profile_id": cfg.profile_id},
            "web_view_properties": {"url": cfg.ad_url},
        }]
    }
    r = requests.post(
        f"{BASE_URL}/adaccounts/{cfg.ad_account_id}/creatives",
        headers=auth.headers(), json=payload, timeout=15,
    )
    data = _check(r, "create_creative")
    creative_id = data["creatives"][0]["creative"]["id"]
    log.info("Creative created: %s → %s", name, creative_id)
    return creative_id


# ── Campaign ──────────────────────────────────────────────────────────────────

def create_campaign(auth: SnapAuth, cfg: FunnelConfig, name: str, start_time: str) -> str:
    """
    Создаёт SALES кампанию с Smart Budget (CBO):
    - бюджет на уровне кампании, распределяется между адсетами автоматически
    - pacing_level: CAMPAIGN + shared_properties (обязательно для CBO)
    - bid_strategy: LOWEST_COST_WITH_MAX_BID с cap $target_cost_usd
    Статус PAUSED — включается вручную после проверки.
    """
    payload = {
        "campaigns": [{
            "name": name,
            "ad_account_id": cfg.ad_account_id,
            "objective_v2_properties": {"objective_v2_type": "SALES"},
            "status": "PAUSED",
            "start_time": start_time,
            "buy_model": "AUCTION",
            "pacing_level": "CAMPAIGN",
            "daily_budget_micro": cfg.campaign_budget_micro,
            "shared_properties": {
                "shared_optimization_goal": cfg.optimization_goal,
                "shared_ad_squad_bid_strategy": "LOWEST_COST_WITH_MAX_BID",
                "shared_pixel_id": cfg.pixel_id,
                "shared_conversion_window": "SWIPE_7DAY",
            },
        }]
    }
    r = requests.post(
        f"{BASE_URL}/adaccounts/{cfg.ad_account_id}/campaigns",
        headers=auth.headers(), json=payload, timeout=15,
    )
    data = _check(r, "create_campaign")
    campaign_id = data["campaigns"][0]["campaign"]["id"]
    log.info("Campaign created: %s → %s ($%.0f/day CBO Smart Budget)", name, campaign_id, cfg.campaign_budget_usd)
    return campaign_id


# ── Ad Squad ──────────────────────────────────────────────────────────────────

def create_ad_squad(
    auth: SnapAuth,
    cfg: FunnelConfig,
    campaign_id: str,
    name: str,
    start_time: str,
) -> str:
    """
    Создаёт Ad Squad для CBO кампании:
    - Бюджет на уровне кампании (здесь не указывается)
    - bid_strategy: LOWEST_COST_WITH_MAX_BID с bid_cap = target_cost_usd
    - Smart Targeting: auto_expansion_type SMART_TARGETING
    - Гео: список стран из конфига
    - Возраст: min_age+
    """
    # demographics: age и language ДОЛЖНЫ быть в одном объекте с operation INCLUDE
    demo: dict = {"min_age": cfg.min_age, "operation": "INCLUDE"}
    if cfg.languages:
        demo["languages"] = cfg.languages

    targeting: dict = {
        "regulated_content": False,
        "geos": cfg.geos,
        "demographics": [demo],
    }
    if cfg.smart_targeting:
        targeting["auto_expansion_options"] = {
            "auto_expansion_type": "SMART_TARGETING",
        }

    payload = {
        "adsquads": [{
            "name": name,
            "campaign_id": campaign_id,
            "type": "SNAP_ADS",
            "status": "ACTIVE",
            "targeting": targeting,
            "placement_v2": {"config": "AUTOMATIC"},
            "billing_event": "IMPRESSION",
            "bid_strategy": "LOWEST_COST_WITH_MAX_BID",
            "bid_micro": cfg.target_cost_micro,
            "optimization_goal": cfg.optimization_goal,
            "pixel_id": cfg.pixel_id,
            "conversion_window": "SWIPE_7DAY",
            "start_time": start_time,
        }]
    }
    r = requests.post(
        f"{BASE_URL}/campaigns/{campaign_id}/adsquads",
        headers=auth.headers(), json=payload, timeout=15,
    )
    data = _check(r, "create_ad_squad")
    ad_squad_id = data["adsquads"][0]["adsquad"]["id"]
    log.info("Ad Squad created: %s → %s (bid cap=$%.0f)", name, ad_squad_id, cfg.target_cost_usd)
    return ad_squad_id


# ── Ad ────────────────────────────────────────────────────────────────────────

def create_ad(
    auth: SnapAuth,
    cfg: FunnelConfig,
    ad_squad_id: str,
    creative_id: str,
    name: str,
) -> str:
    """Создаёт объявление. Возвращает ad_id."""
    payload = {
        "ads": [{
            "name": name,
            "ad_squad_id": ad_squad_id,
            "creative_id": creative_id,
            "type": "REMOTE_WEBPAGE",
            "status": "ACTIVE",
        }]
    }
    r = requests.post(
        f"{BASE_URL}/adsquads/{ad_squad_id}/ads",
        headers=auth.headers(), json=payload, timeout=15,
    )
    data = _check(r, "create_ad")
    ad_id = data["ads"][0]["ad"]["id"]
    log.info("Ad created: %s → %s", name, ad_id)
    return ad_id
