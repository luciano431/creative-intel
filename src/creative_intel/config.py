"""Carga de configuración: variables de entorno, universo de competidores y taxonomía."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", ROOT / "config"))


@dataclass
class Settings:
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'creative_intel.db'}")
    assets_dir: Path = Path(os.getenv("ASSETS_DIR", ROOT / "data" / "assets"))
    classify_model: str = os.getenv("CLASSIFY_MODEL", "claude-opus-5-5")
    synth_model: str = os.getenv("SYNTH_MODEL", "claude-opus-5-5")
    anthropic_fallbacks: str = os.getenv("ANTHROPIC_FALLBACKS", "default")
    meta_access_token: str = os.getenv("META_ACCESS_TOKEN", "")
    meta_graph_version: str = os.getenv("META_GRAPH_VERSION", "v21.0")
    serpapi_key: str = os.getenv("SERPAPI_KEY", "")
    basecamp_account_id: str = os.getenv("BASECAMP_ACCOUNT_ID", "")
    basecamp_project_id: str = os.getenv("BASECAMP_PROJECT_ID", "")
    basecamp_vault_id: str = os.getenv("BASECAMP_VAULT_ID", "")
    basecamp_todolist_id: str = os.getenv("BASECAMP_TODOLIST_ID", "")
    basecamp_access_token: str = os.getenv("BASECAMP_ACCESS_TOKEN", "")
    basecamp_refresh_token: str = os.getenv("BASECAMP_REFRESH_TOKEN", "")
    basecamp_client_id: str = os.getenv("BASECAMP_CLIENT_ID", "")
    basecamp_client_secret: str = os.getenv("BASECAMP_CLIENT_SECRET", "")
    basecamp_user_agent: str = os.getenv("BASECAMP_USER_AGENT", "creative-intel (contact@example.com)")
    slack_webhook_url: str = os.getenv("SLACK_WEBHOOK_URL", "")
    make_webhook_url: str = os.getenv("MAKE_WEBHOOK_URL", "")
    api_token: str = os.getenv("API_TOKEN", "")


settings = Settings()


@dataclass
class CompetitorConfig:
    slug: str
    name: str
    country: str
    tier: str  # local | global | own
    website: str = ""
    meta_page_id: str = ""
    google_advertiser_id: str = ""
    google_domain: str = ""
    google_domains: dict[str, str] = field(default_factory=dict)  # dominio por país (ES: generali.es, IT: generali.it)
    ad_countries: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    active: bool = True

    @property
    def is_own(self) -> bool:
        return self.tier == "own"


def load_competitors(path: Path | None = None) -> list[CompetitorConfig]:
    """Aplana el YAML jerárquico (país > tier > competidor) en una lista."""
    path = path or CONFIG_DIR / "competitors.yaml"
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: list[CompetitorConfig] = []
    for country in data.get("countries", []):
        code = country["code"]
        own = country.get("own")
        if own:
            out.append(_to_competitor(own, code, "own"))
        for tier in ("local", "global"):
            for item in (country.get("competitors") or {}).get(tier, []) or []:
                out.append(_to_competitor(item, code, tier))
    return out


def _to_competitor(item: dict[str, Any], country: str, tier: str) -> CompetitorConfig:
    return CompetitorConfig(
        slug=item["slug"],
        name=item["name"],
        country=country,
        tier=tier,
        website=item.get("website", "") or "",
        meta_page_id=str(item.get("meta_page_id", "") or ""),
        google_advertiser_id=str(item.get("google_advertiser_id", "") or ""),
        google_domain="" if isinstance(item.get("google_domain"), dict) else str(item.get("google_domain", "") or ""),
        google_domains={str(k).upper(): str(v) for k, v in (item.get("google_domain") or {}).items()} if isinstance(item.get("google_domain"), dict) else {},
        ad_countries=list(item.get("ad_countries") or [country]),
        products=list(item.get("products") or []),
        active=bool(item.get("active", True)),
    )


@dataclass
class Taxonomy:
    angle_families: dict[str, list[str]]
    hook_types: list[str]
    formats: list[str]
    placements: list[str]
    funnel_stages: list[str]
    cta_types: list[str]
    creative_styles: list[str]
    products: list[str]
    confidence_levels: list[str]
    white_space_threshold_pct: float = 6.0

    @property
    def angles(self) -> list[str]:
        return [a for fam in self.angle_families.values() for a in fam]

    @property
    def families(self) -> list[str]:
        return list(self.angle_families.keys())

    def family_of(self, angle: str | None) -> str | None:
        for fam, angles in self.angle_families.items():
            if angle in angles:
                return fam
        return None


def load_taxonomy(path: Path | None = None) -> Taxonomy:
    path = path or CONFIG_DIR / "taxonomy.yaml"
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Taxonomy(
        angle_families={k: list(v) for k, v in data["angle_families"].items()},
        hook_types=list(data["hook_types"]),
        formats=list(data["formats"]),
        placements=list(data["placements"]),
        funnel_stages=list(data["funnel_stages"]),
        cta_types=list(data["cta_types"]),
        creative_styles=list(data["creative_styles"]),
        products=list(data["products"]),
        confidence_levels=list(data.get("confidence_levels", ["low", "medium", "high"])),
        white_space_threshold_pct=float(data.get("white_space_threshold_pct", 6)),
    )
