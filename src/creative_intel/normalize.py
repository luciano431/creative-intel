"""Normalización: fingerprint, deduplicación, upsert e histórico de longevidad."""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Iterable

import httpx
from sqlalchemy.orm import Session

from .collectors.base import RawCreative
from .config import settings
from .db import Competitor, Creative, Run, Sighting

_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    return _WS.sub(" ", (text or "").strip().lower())


def fingerprint(raw: RawCreative) -> str:
    """Identidad estable de un creativo.

    Si la fuente da un ID externo (Ad Library ID, creative ID de Google) se usa eso.
    Si no, se usa texto normalizado + primer medio: dos anuncios con el mismo copy y la
    misma imagen son el mismo creativo aunque aparezcan en placements distintos.
    """
    if raw.external_id:
        key = f"{raw.competitor_slug}|{raw.platform}|id:{raw.external_id}"
    else:
        media = raw.media_urls[0] if raw.media_urls else raw.thumbnail_url
        key = f"{raw.competitor_slug}|{raw.platform}|{normalize_text(raw.text_blob)}|{media}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def dedupe(raws: Iterable[RawCreative]) -> list[RawCreative]:
    seen: dict[str, RawCreative] = {}
    for r in raws:
        fp = fingerprint(r)
        if fp in seen:
            prev = seen[fp]
            prev.placements = sorted(set(prev.placements) | set(r.placements))
            prev.countries = sorted(set(prev.countries) | set(r.countries))
            if r.first_seen and (not prev.first_seen or r.first_seen < prev.first_seen):
                prev.first_seen = r.first_seen
            if r.last_seen and (not prev.last_seen or r.last_seen > prev.last_seen):
                prev.last_seen = r.last_seen
        else:
            seen[fp] = r
    return list(seen.values())


def upsert_creatives(
    session: Session,
    run: Run,
    raws: Iterable[RawCreative],
    seen_on: dt.date,
    download_media: bool = False,
) -> Counter:
    """Inserta/actualiza creativos y registra una Sighting por run.

    Reglas de longevidad:
    - first_seen = fecha declarada por la fuente si existe, si no la primera vez que lo vimos.
    - last_seen = fecha de hoy si está activo; nunca retrocede.
    """
    stats: Counter = Counter()
    competitors = {c.slug: c for c in session.query(Competitor).all()}
    for raw in dedupe(raws):
        comp = competitors.get(raw.competitor_slug)
        if comp is None:
            stats["unknown_competitor"] += 1
            continue
        fp = fingerprint(raw)
        creative = session.query(Creative).filter_by(fingerprint=fp).one_or_none()
        declared_first = raw.first_seen
        # Finalizado sin fecha de fin: no se inventa duración (queda igual al inicio, "sin dato")
        declared_last = raw.last_seen or (seen_on if raw.is_active else (raw.first_seen or None))
        if creative is None:
            creative = Creative(
                fingerprint=fp,
                competitor_id=comp.id,
                platform=raw.platform,
                source=raw.source,
                external_id=raw.external_id,
                first_seen=declared_first or seen_on,
                last_seen=declared_last or seen_on,
            )
            session.add(creative)
            stats["new"] += 1
        else:
            stats["updated"] += 1
            if declared_first and declared_first < creative.first_seen:
                creative.first_seen = declared_first
            if declared_last and declared_last > creative.last_seen:
                creative.last_seen = declared_last
        creative.format = raw.format or creative.format
        creative.placements = sorted(set(creative.placements or []) | set(raw.placements))
        creative.headline = raw.headline or creative.headline
        creative.body_text = raw.body_text or creative.body_text
        creative.description = raw.description or creative.description
        creative.cta_text = raw.cta_text or creative.cta_text
        creative.landing_url = raw.landing_url or creative.landing_url
        creative.media_urls = raw.media_urls or creative.media_urls or []
        creative.snapshot_url = raw.snapshot_url or creative.snapshot_url
        creative.countries = sorted(set(creative.countries or []) | set(raw.countries))
        if raw.engagement:
            creative.engagement = raw.engagement
        creative.status = "active" if raw.is_active else "inactive"
        creative.raw = raw.raw or creative.raw
        session.flush()
        if download_media and raw.thumbnail_url and not creative.thumbnail_path:
            creative.thumbnail_path = _download(raw.thumbnail_url, fp) or ""
        sighting = session.query(Sighting).filter_by(creative_id=creative.id, run_id=run.id).one_or_none()
        if sighting is None:
            session.add(Sighting(creative_id=creative.id, run_id=run.id, seen_on=seen_on, is_active=raw.is_active))
        else:
            sighting.seen_on = seen_on
            sighting.is_active = raw.is_active
    session.flush()
    return stats


def mark_unseen_inactive(session: Session, run: Run, competitor_ids: Iterable[int], platforms: Iterable[str]) -> int:
    """Creativos activos que este run NO volvió a ver pasan a inactive (last_seen no se toca)."""
    seen_ids = {s.creative_id for s in session.query(Sighting).filter_by(run_id=run.id).all()}
    q = (
        session.query(Creative)
        .filter(Creative.competitor_id.in_(list(competitor_ids)))
        .filter(Creative.platform.in_(list(platforms)))
        .filter(Creative.status == "active")
    )
    n = 0
    for c in q.all():
        if c.id not in seen_ids:
            c.status = "inactive"
            n += 1
    session.flush()
    return n


def _download(url: str, fp: str) -> str | None:
    try:
        settings.assets_dir.mkdir(parents=True, exist_ok=True)
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        ext = ".png" if "png" in ctype else ".gif" if "gif" in ctype else ".webp" if "webp" in ctype else ".jpg"
        path = settings.assets_dir / f"{fp}{ext}"
        path.write_bytes(resp.content)
        return str(path)
    except Exception:
        return None


def data_qa_flags(creative: Creative) -> list[str]:
    """Checkpoint humano 1: qué debería mirar una persona antes de analizar."""
    flags: list[str] = []
    if not creative.text_blob() and not (creative.raw or {}).get("text_in_image"):
        flags.append("sin_texto")
    if not creative.media_urls and not creative.thumbnail_path:
        flags.append("sin_media")
    if creative.format == "desconocido":
        flags.append("formato_desconocido")
    if creative.days_active > 365:
        flags.append("longevidad_sospechosa")
    if creative.source in ("meta_web", "google_web"):
        flags.append("scraping_best_effort")
    if (creative.raw or {}).get("removed"):
        flags.append("contenido_no_disponible")
    return flags


def _text_blob(self: Creative) -> str:
    return " ".join(p for p in (self.headline, self.body_text, self.description, self.cta_text) if p).strip()


Creative.text_blob = _text_blob  # type: ignore[attr-defined]


def ensure_assets_dir() -> Path:
    settings.assets_dir.mkdir(parents=True, exist_ok=True)
    return settings.assets_dir
