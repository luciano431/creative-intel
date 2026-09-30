"""Importación manual (CSV o JSON). Sirve para:
- cargar creativos que el scraping no alcanzó,
- cargar NUESTROS propios anuncios (competidor con tier=own) con resultados reales,
- correr el sistema sin ninguna API configurada.

Columnas CSV: competitor_slug, platform, external_id, format, placements (a|b), headline,
body_text, description, cta_text, landing_url, media_urls (a|b), thumbnail_url, countries (a|b),
first_seen (YYYY-MM-DD), last_seen, is_active (true/false), likes, comments, shares
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from ..config import CompetitorConfig
from .base import RawCreative, parse_date

INBOX = Path("data/inbox")


def _split(v: Any) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x]
    return [x.strip() for x in str(v).split("|") if x.strip()]


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "si", "sí", "yes", "activo", "active", "")


def row_to_raw(row: dict[str, Any], default_slug: str | None = None, default_platform: str | None = None) -> RawCreative:
    slug = row.get("competitor_slug") or default_slug
    platform = row.get("platform") or default_platform
    if not slug or not platform:
        raise ValueError("Cada fila necesita competitor_slug y platform (meta|google)")
    engagement = {k: int(row[k]) for k in ("likes", "comments", "shares", "views") if str(row.get(k, "")).strip().isdigit()}
    if isinstance(row.get("engagement"), dict):
        engagement.update(row["engagement"])
    return RawCreative(
        competitor_slug=slug,
        platform=platform,
        source=row.get("source") or "manual",
        external_id=str(row.get("external_id") or ""),
        format=row.get("format") or "desconocido",
        placements=_split(row.get("placements")) or ["desconocido"],
        headline=row.get("headline") or "",
        body_text=row.get("body_text") or "",
        description=row.get("description") or "",
        cta_text=row.get("cta_text") or "",
        landing_url=row.get("landing_url") or "",
        media_urls=_split(row.get("media_urls")),
        thumbnail_url=row.get("thumbnail_url") or "",
        snapshot_url=row.get("snapshot_url") or "",
        countries=_split(row.get("countries")),
        engagement=engagement,
        first_seen=parse_date(row.get("first_seen")),
        last_seen=parse_date(row.get("last_seen")),
        is_active=_bool(row.get("is_active", True)),
        raw={k: v for k, v in row.items() if k not in ("raw",)} | (row.get("raw") or {}),
    )


def load_manual_file(path: str | Path, default_slug: str | None = None, default_platform: str | None = None) -> list[RawCreative]:
    path = Path(path)
    rows: Iterable[dict[str, Any]]
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("creatives", [])
    else:
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    return [row_to_raw(r, default_slug, default_platform) for r in rows]


class ManualCollector:
    """Lee todo lo que haya en data/inbox/<slug>/*.csv|json."""

    name = "manual"
    platform = "any"

    def __init__(self, inbox: Path | None = None):
        self.inbox = inbox or INBOX

    def supports(self, competitor: CompetitorConfig) -> bool:
        return (self.inbox / competitor.slug).is_dir()

    def collect(self, competitor: CompetitorConfig) -> list[RawCreative]:
        out: list[RawCreative] = []
        for f in sorted((self.inbox / competitor.slug).glob("*")):
            if f.suffix.lower() in (".csv", ".json"):
                out.extend(load_manual_file(f, default_slug=competitor.slug))
        return out
