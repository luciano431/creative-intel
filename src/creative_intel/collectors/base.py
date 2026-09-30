"""Contrato común de los colectores. Cada fuente devuelve RawCreative; el resto del sistema no sabe de dónde vino."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import CompetitorConfig


@dataclass
class RawCreative:
    competitor_slug: str
    platform: str  # meta | google
    source: str  # meta_api | meta_web | google_serpapi | google_web | manual | own
    external_id: str = ""
    format: str = "desconocido"
    placements: list[str] = field(default_factory=list)
    headline: str = ""
    body_text: str = ""
    description: str = ""
    cta_text: str = ""
    landing_url: str = ""
    media_urls: list[str] = field(default_factory=list)
    thumbnail_url: str = ""
    snapshot_url: str = ""
    countries: list[str] = field(default_factory=list)
    engagement: dict[str, Any] = field(default_factory=dict)  # solo señales visibles
    first_seen: dt.date | None = None
    last_seen: dt.date | None = None
    is_active: bool = True
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def text_blob(self) -> str:
        return " ".join(p for p in (self.headline, self.body_text, self.description, self.cta_text) if p).strip()


class Collector(Protocol):
    name: str
    platform: str

    def supports(self, competitor: CompetitorConfig) -> bool: ...

    def collect(self, competitor: CompetitorConfig) -> list[RawCreative]: ...


def parse_date(value: Any) -> dt.date | None:
    """Acepta ISO 8601 con o sin hora, 'YYYY-MM-DD', o None."""
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(text[: len(fmt) + 6 if "%z" in fmt else len(text)], fmt).date()
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None
