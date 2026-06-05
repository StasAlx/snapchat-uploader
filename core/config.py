"""
FunnelConfig — параметры одной воронки для Snapchat.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import List
import yaml


@dataclass
class FunnelConfig:
    name: str

    # Snapchat
    ad_account_id: str
    pixel_id: str
    profile_id: str

    # Google Drive
    gdrive_root_folder_id: str

    # Шаблон имени кампании (дата _DDMMYY_HHMMSS добавляется автоматически)
    campaign_name_template: str

    # Тексты объявления
    ad_url: str
    headline: str            # до 34 символов
    brand_name: str          # название бренда на объявлении
    call_to_action: str = "MORE"

    # Бюджет кампании в USD/день (конвертируется в micro: $1 = 1_000_000)
    campaign_budget_usd: float = 300.0

    # Target Cost на конверсию (USD)
    target_cost_usd: float = 60.0

    # Таргетинг — гео (список стран, обязателен для CBO)
    countries: List[str] = field(default_factory=lambda: [
        "us","gb","ca","au","nz","ie","de","fr","it","es","nl","se","no","dk","fi","be","at","ch","pl","pt",
        "ae","sa","kw","qa","bh","om","tr","eg","ma",
        "sg","jp","hk","in","th","my","id",
        "br","mx","ar","co","cl","za","ng","ke",
    ])

    # Smart Targeting (авторасширение аудитории)
    smart_targeting: bool = True

    # Язык (ISO коды)
    languages: List[str] = field(default_factory=lambda: ["en"])

    # Минимальный возраст
    min_age: int = 21

    # Событие пикселя для оптимизации
    optimization_goal: str = "PIXEL_PURCHASE"

    # Файлов за один прогон (делятся на 2 адсета)
    batch_size: int = 10

    @property
    def campaign_budget_micro(self) -> int:
        return int(self.campaign_budget_usd * 1_000_000)

    @property
    def target_cost_micro(self) -> int:
        return int(self.target_cost_usd * 1_000_000)

    @property
    def geos(self) -> list:
        """Таргетинг geo для Snapchat API — lowercase ISO коды. Пустой список = всё."""
        return [{"country_code": c.lower()} for c in self.countries]


def load_config(name_or_path: str) -> FunnelConfig:
    path = Path(name_or_path)
    if not path.suffix:
        path = Path(__file__).parent.parent / "configs" / f"{name_or_path}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return FunnelConfig(
        name=data["name"],
        ad_account_id=str(data["ad_account_id"]),
        pixel_id=str(data["pixel_id"]),
        profile_id=str(data["profile_id"]),
        gdrive_root_folder_id=str(data["gdrive_root_folder_id"]),
        campaign_name_template=data["campaign_name_template"],
        ad_url=data["ad_url"],
        headline=data["headline"],
        brand_name=data["brand_name"],
        call_to_action=data.get("call_to_action", "MORE"),
        campaign_budget_usd=float(data.get("campaign_budget_usd", 300.0)),
        target_cost_usd=float(data.get("target_cost_usd", 60.0)),
        countries=data.get("countries", []),
        smart_targeting=bool(data.get("smart_targeting", True)),
        languages=data.get("languages", ["en"]),
        optimization_goal=data.get("optimization_goal", "PIXEL_PURCHASE"),
        min_age=int(data.get("min_age", 21)),
        batch_size=int(data.get("batch_size", 10)),
    )
