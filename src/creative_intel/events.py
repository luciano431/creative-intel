"""Modo evento: estudiar una edición pasada de un evento comercial y armar la guía del cliente.

    radar evento cyberlunes                 # última edición cargada en events.yaml
    radar evento black-friday --edition 2025 --mock-ai

Pasos: recolectar (Meta, ventana de fechas: marcas del cliente + quien mencionó el evento) →
clasificar (misma IA que el informe mensual) → analizar (timing, ofertas, urgencia) → guía → informe.
Cada edición se guarda como un run propio ("ev-<evento>-<edición>") y no se mezcla con los meses.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .collectors.base import RawCreative
from .config import EventConfig, EventEdition, load_client, load_competitors, load_taxonomy, settings
from .db import Competitor, get_or_create_run, sync_competitors
from .normalize import upsert_creatives


def event_period(event: EventConfig, edition: EventEdition) -> str:
    return f"ev-{event.key}-{edition.label}"


def live_edition(event: EventConfig, today: dt.date | None = None, lookback_days: int = 60) -> EventEdition:
    """Edición en curso: lo que hoy está activo y arrancó en los últimos `lookback_days` días.

    Cada corrida suma al mismo archivo (mismo label por mes), así se ve cómo arranca el mercado antes del evento.
    """
    today = today or dt.date.today()
    start = event.next_start if event.next_start and event.next_start >= today - dt.timedelta(days=15) else None
    return EventEdition(label=f"vivo-{today:%Y-%m}", date_from=today - dt.timedelta(days=lookback_days), date_to=today,
                        start=start, end=event.next_end if start else None)


NO_ADS_HELP = ("Fuera de la Unión Europea, la Biblioteca de Meta solo muestra los anuncios mientras están activos: "
               "las ediciones pasadas ya no están. Corré el modo en vivo (--live) unos días antes y durante el evento; "
               "el Radar guarda su propio archivo y el año que viene esta edición ya va a estar para estudiar.")


def _register_market_advertisers(session: Session, raws: list[RawCreative]) -> None:
    """Los anunciantes encontrados por palabra clave que no están configurados quedan como 'mercado'."""
    known = {c.slug for c in session.query(Competitor).all()}
    for r in raws:
        if r.competitor_slug in known:
            continue
        session.add(Competitor(slug=r.competitor_slug, name=(r.raw or {}).get("page_name") or r.competitor_slug, country=(r.countries or ["UY"])[0],
                               tier="mercado", active=True, ad_countries=list(r.countries or [])))
        known.add(r.competitor_slug)
    session.flush()


def collect_event(session: Session, event: EventConfig, edition: EventEdition, per_brand: int | None = None, market: int = 30,
                  collector: Any = None) -> dict[str, Any]:
    from .collectors.meta_web import MetaAdLibraryWebCollector

    run = get_or_create_run(session, event_period(event, edition))
    configs = load_competitors()
    sync_competitors(session, configs)
    col = collector or MetaAdLibraryWebCollector()
    per_brand = per_brand or settings.top_ads_per_brand * 2
    by_name = {c.name.lower(): c for c in configs}
    raws: list[RawCreative] = []
    errors: list[str] = []
    live = edition.label.startswith("vivo-")
    for c in configs:
        if not c.active or not c.meta_page_id or c.tier not in ("own", "local"):
            continue
        for country in (c.ad_countries or [c.country]):
            try:
                # En vivo: lo activo hoy de cada marca, sin importar cuándo arrancó
                raws += col.collect_window(c, country, None if live else edition.date_from, None if live else edition.date_to, per_brand)
            except Exception as exc:
                errors.append(f"{c.slug}: {exc}")
    keywords = event.keywords[:2]
    focus = load_client().focus_keywords  # del mercado, solo los que venden lo mismo que el cliente
    for country in event.countries:
        for kw in keywords:
            try:
                found = col.collect_keyword(kw, country, edition.date_from, edition.date_to, max(5, market // max(1, len(keywords))),
                                            **({"focus": focus} if focus else {}))
            except Exception as exc:
                errors.append(f"«{kw}» {country}: {exc}")
                continue
            for r in found:  # si el anunciante es una marca configurada, va con su nombre
                known = by_name.get(((r.raw or {}).get("page_name") or "").lower())
                if known:
                    r.competitor_slug = known.slug
            raws += found
    _register_market_advertisers(session, raws)
    up = upsert_creatives(session, run, raws, dt.date.today(), download_media=True)
    run.status = "collected"
    run.stats = {**(run.stats or {}), "collect": {"ads": len(raws), **dict(up), "errors": errors}}
    session.flush()
    return run.stats["collect"]


def run_event(session: Session, event: EventConfig, edition: EventEdition, mock_ai: bool = False, skip_collect: bool = False,
              out_dir: str | Path | None = None) -> dict[str, Any]:
    from . import pipeline
    from .ai.client import check_api
    from .ai.event import mock_event_plan, synthesize_event
    from .analysis.event import analyze_event
    from .analysis.metrics import start_known
    from .analysis.signals import signal_list
    from .config import REPORTS_DIR, load_brand
    from .report.build import _env, render_assets, top_creatives

    if not mock_ai:
        check_api()
    summary: dict[str, Any] = {"event": event.name, "edition": edition.label}
    period = event_period(event, edition)
    if not skip_collect:
        summary["collect"] = collect_event(session, event, edition)
        session.commit()
    summary["classify"] = pipeline.classify(session, period, mock=mock_ai)
    session.commit()
    taxonomy = load_taxonomy()
    client = load_client()
    run = get_or_create_run(session, period)
    items = pipeline.items_for(session, run, taxonomy)
    if not items:
        stats = (summary.get("collect") or {})
        errs = "; ".join(stats.get("errors") or [])[:600]
        raise pipeline.NoData(f"No se encontraron anuncios de {event.name} {edition.label}. "
                              + (f"Errores al recolectar: {errs}. " if errs else "")
                              + ("" if edition.label.startswith("vivo-") else NO_ADS_HELP))
    for it in items:
        it["signals"] = signal_list(it, start_known(it))
    metrics = analyze_event(items, edition.start, edition.end, own_name=client.name)
    next_dates = {"start": event.next_start.isoformat() if event.next_start else None,
                  "end": event.next_end.isoformat() if event.next_end else None}
    if mock_ai:
        plan = mock_event_plan(event.name, metrics, client.name)
    else:
        labels = {k: taxonomy.label(k) for k in taxonomy.labels} if getattr(taxonomy, "labels", None) else {}
        plan = synthesize_event(event.name, metrics, items, client, next_dates, labels=labels)
    run.metrics = {"event": metrics, "plan": plan}
    run.status = "draft"
    session.flush()

    out = Path(out_dir or Path(REPORTS_DIR) / "eventos" / f"{event.key}-{edition.label}")
    refs = top_creatives([it for it in items if it.get("tier") != "own"], n=9, per_competitor=2)
    render_assets(refs, out)
    brand = load_brand()
    html = _env().get_template("event.html.j2").render(
        live=edition.label.startswith("vivo-"), event=event, edition=edition, client=client, brand=brand, lbl=taxonomy.label, event_metrics=metrics, plan=plan,
        refs=refs, next_dates=next_dates, generated_at=dt.datetime.now().strftime("%d/%m/%Y %H:%M"))
    (out / "report.html").write_text(html, encoding="utf-8")
    summary["ads"] = metrics.get("ads", 0)
    summary["report"] = str(out / "report.html")
    return summary
