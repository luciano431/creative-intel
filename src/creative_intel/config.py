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
# Multi-cliente: RADAR_CLIENT=<slug> usa config/clients/<slug>/ y datos/informes separados.
# Sin RADAR_CLIENT se usa config/ (cliente por defecto) para no romper instalaciones existentes.
RADAR_CLIENT = os.getenv("RADAR_CLIENT", "").strip()
CLIENTS_DIR = ROOT / "config" / "clients"
CONFIG_DIR = Path(os.getenv("CONFIG_DIR") or (CLIENTS_DIR / RADAR_CLIENT if RADAR_CLIENT else ROOT / "config"))
DATA_DIR = ROOT / "data" / "clients" / RADAR_CLIENT if RADAR_CLIENT else ROOT / "data"
REPORTS_DIR = Path(os.getenv("REPORTS_DIR") or (ROOT / "reports" / RADAR_CLIENT if RADAR_CLIENT else "reports"))


def _ai_provider() -> str:
    """openai | anthropic. Sin AI_PROVIDER: OpenAI si solo hay clave de OpenAI; si no, Anthropic."""
    explicit = os.getenv("AI_PROVIDER", "").strip().lower()
    if explicit in ("openai", "anthropic"):
        return explicit
    return "openai" if os.getenv("OPENAI_API_KEY") and not os.getenv("ANTHROPIC_API_KEY") else "anthropic"


AI_PROVIDER = _ai_provider()


@dataclass
class Settings:
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / ('radar.db' if RADAR_CLIENT else 'creative_intel.db')}")
    assets_dir: Path = Path(os.getenv("ASSETS_DIR", DATA_DIR / "assets"))
    ai_provider: str = AI_PROVIDER
    # Clasificar: modelo barato con visión. Escribir el plan: el modelo más capaz.
    classify_model: str = (os.getenv("OPENAI_CLASSIFY_MODEL", "gpt-5.4-mini") if AI_PROVIDER == "openai"
                           else os.getenv("CLASSIFY_MODEL", "claude-sonnet-5-5"))
    synth_model: str = (os.getenv("OPENAI_SYNTH_MODEL", "gpt-5.4") if AI_PROVIDER == "openai"
                        else os.getenv("SYNTH_MODEL", "claude-opus-5-5"))
    # Anuncios por marca y por plataforma: en Meta los más vistos (orden por impresiones), en Google los primeros.
    top_ads_per_brand: int = int(os.getenv("TOP_ADS_PER_BRAND", "5"))
    # Tope de gasto de IA por corrida (por cliente). Al llegar, la clasificación se detiene y guarda lo hecho.
    max_ai_usd_per_run: float = float(os.getenv("MAX_AI_USD_PER_RUN", "2.00"))
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
    meta_ad_account_id: str = ""  # solo el cliente: cuenta publicitaria para traer sus resultados (act_123...)

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
        meta_ad_account_id=str(item.get("meta_ad_account_id", "") or ""),
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
    labels: dict[str, str] = field(default_factory=dict)

    def label(self, value: Any) -> str:
        """Nombre en castellano para mostrar en el informe."""
        if value is None or value == "":
            return "Sin dato"
        v = str(value)
        return self.labels.get(v) or v.replace("_", " ").capitalize()

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
    # Cada cliente puede tener su taxonomía (otra industria); si no, se usa la general.
    path = path or next((p for p in (CONFIG_DIR / "taxonomy.yaml", ROOT / "config" / "taxonomy.yaml") if p.is_file()), CONFIG_DIR / "taxonomy.yaml")
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
        labels={str(k): str(v) for k, v in (data.get("labels") or {}).items()},
    )


@dataclass
class Brand:
    app_name: str = "Radar creativo de Ranker"
    agency: str = "Ranker"
    agency_url: str = ""
    logo_url: str = ""
    font_family: str = '-apple-system, "Segoe UI", Roboto, sans-serif'
    font_url: str = ""
    heading_font: str = ""
    logo_svg: str = ""  # contenido SVG inline (se lee del archivo indicado en brand.yaml)
    colors: dict[str, str] = field(default_factory=dict)


BRAND_DEFAULT_COLORS = {
    "primary": "#1D1C22", "primary_2": "#13131B", "accent": "#FD6D30", "accent_2": "#22C0E8",
    "teal": "#00D4B0", "yellow": "#FBE9A8", "sky": "#ACEFFF", "warn": "#F28A82", "slate": "#415560",
    "ink": "#1D1C22", "muted": "#6B6A75", "line": "#E8E7EC", "soft": "#F6F5F2", "bg": "#FFFFFF",
}


def load_brand(path: Path | None = None) -> Brand:
    """Marca del informe: primero la del cliente (si tiene brand.yaml propio), si no la global de Ranker."""
    candidates = [path] if path else [CONFIG_DIR / "brand.yaml", ROOT / "config" / "brand.yaml"]
    data: dict[str, Any] = {}
    for p in candidates:
        if p and Path(p).is_file():
            data = yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
            break
    colors = {**BRAND_DEFAULT_COLORS, **{k: str(v) for k, v in (data.get("colors") or {}).items()}}
    svg = ""
    if data.get("logo_svg"):
        p = Path(data["logo_svg"])
        p = p if p.is_absolute() else ROOT / p
        if p.is_file():
            svg = p.read_text(encoding="utf-8")
    return Brand(
        app_name=data.get("app_name", Brand.app_name),
        agency=data.get("agency", Brand.agency),
        agency_url=data.get("agency_url", ""),
        logo_url=data.get("logo_url", "") or "",
        font_family=data.get("font_family", Brand.font_family),
        font_url=data.get("font_url", "") or "",
        heading_font=data.get("heading_font", "") or data.get("font_family", Brand.font_family),
        logo_svg=svg,
        colors=colors,
    )


@dataclass
class ClientInfo:
    """El cliente para el que se arma el plan (cualquier cliente de Ranker)."""
    slug: str
    name: str
    industry: str = ""
    market: str = ""
    description: str = ""
    # Hoja del cliente (client.brief en competitors.yaml): tono, qué empujar, diferencial, promos, qué evitar, público.
    brief: dict[str, Any] = field(default_factory=dict)


def _brief(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = {"notas": raw}
    return {k: v for k, v in (raw or {}).items() if v not in (None, "", [])}


def load_client(path: Path | None = None) -> ClientInfo:
    path = path or CONFIG_DIR / "competitors.yaml"
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    c = data.get("client") or {}
    own = next((cty.get("own") for cty in data.get("countries", []) if cty.get("own")), None) or {}
    first_country = next(iter(data.get("countries", [])), {}) or {}
    return ClientInfo(
        slug=c.get("slug") or own.get("slug") or "cliente",
        name=c.get("name") or own.get("name") or "el cliente",
        industry=c.get("industry", ""),
        market=c.get("market") or first_country.get("name", ""),
        description=c.get("description", ""),
        brief=_brief(c.get("brief")),
    )


# ----------------------------------------------------------------------------- fechas comerciales


@dataclass
class EventEdition:
    label: str
    date_from: Any
    date_to: Any
    start: Any = None
    end: Any = None


@dataclass
class EventConfig:
    key: str
    name: str
    keywords: list[str]
    countries: list[str]
    editions: list[EventEdition]
    next_start: Any = None
    next_end: Any = None

    def edition(self, label: str | None = None) -> EventEdition:
        if not self.editions:
            raise ValueError(f"El evento {self.name} no tiene ediciones cargadas en events.yaml")
        if label is None:
            return self.editions[-1]
        for e in self.editions:
            if e.label == label:
                return e
        raise ValueError(f"No hay edición «{label}» de {self.name}. Las que hay: {', '.join(e.label for e in self.editions)}")


def _date(v: Any):
    import datetime as _dt

    if v in (None, "", "null"):
        return None
    if isinstance(v, _dt.date):
        return v
    return _dt.date.fromisoformat(str(v))


def load_events(config_dir: Path | None = None) -> dict[str, EventConfig]:
    """Eventos generales (config/events.yaml) más los del cliente (config/clients/<slug>/events.yaml), que pisan."""
    raw: dict[str, Any] = {}
    for path in (ROOT / "config" / "events.yaml", (config_dir or CONFIG_DIR) / "events.yaml"):
        if path.exists():
            raw.update((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("events") or {})
    out = {}
    for key, e in raw.items():
        nxt = e.get("next") or {}
        out[key] = EventConfig(
            key=key,
            name=e.get("name") or key,
            keywords=[str(k) for k in e.get("keywords") or []],
            countries=[str(c).upper() for c in e.get("countries") or ["UY"]],
            editions=[EventEdition(label=str(x["label"]), date_from=_date(x.get("from")), date_to=_date(x.get("to")),
                                   start=_date(x.get("start")), end=_date(x.get("end"))) for x in e.get("editions") or []],
            next_start=_date(nxt.get("start")),
            next_end=_date(nxt.get("end")),
        )
    return out
