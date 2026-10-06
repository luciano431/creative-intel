"""Orquestación de los pasos del ciclo mensual. La CLI y la API llaman a estas funciones;
Make puede disparar cada paso por webhook (ver api.py) o el cron de GitHub Actions corre el ciclo completo.

Ciclo (día 20-26 del mes):
  collect -> classify -> analyze -> synthesize -> report(draft) -> [QA humana] -> publish
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from .ai import classify_creative, heuristic_classify, mock_synthesis, synthesize
from .analysis import build_metrics, creative_to_item, group_concepts
from .collectors import get_collectors, load_manual_file
from .collectors.base import RawCreative
from .config import CompetitorConfig, Taxonomy, load_competitors, load_taxonomy, settings
from .db import Classification, Competitor, Creative, Run, Sighting, Synthesis, get_or_create_run, sync_competitors
from .normalize import mark_unseen_inactive, upsert_creatives
from .publish import notify, publish_to_basecamp
from .report import build_report_context, render_report
from .report.build import _env
from .review import latest_synthesis

log = logging.getLogger("creative_intel")


def current_period(today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    return today.strftime("%Y-%m")


def previous_period(period: str) -> str:
    y, m = (int(x) for x in period.split("-"))
    m -= 1
    if m == 0:
        m, y = 12, y - 1
    return f"{y:04d}-{m:02d}"


def sync(session: Session) -> int:
    return sync_competitors(session, load_competitors())


# ----------------------------------------------------------------------------- collect
def collect(
    session: Session,
    period: str,
    sources: list[str] | None = None,
    only: Iterable[str] | None = None,
    seen_on: dt.date | None = None,
    download_media: bool = True,
    extra_raws: list[RawCreative] | None = None,
) -> dict[str, Any]:
    seen_on = seen_on or dt.date.today()
    run = get_or_create_run(session, period)
    configs = [c for c in load_competitors() if c.active and (not only or c.slug in only)]
    sync_competitors(session, configs)
    collectors = get_collectors(sources)
    stats: Counter = Counter()
    errors: list[str] = []
    all_raws: list[RawCreative] = list(extra_raws or [])
    touched_platforms: set[str] = set()
    for comp in configs:
        for col in collectors:
            if not col.supports(comp):
                continue
            try:
                raws = col.collect(comp)
            except Exception as exc:  # una fuente rota no tumba el ciclo
                errors.append(f"{col.name}/{comp.slug}: {exc}")
                log.warning("collector %s falló para %s: %s", col.name, comp.slug, exc)
                continue
            stats[f"{col.name}"] += len(raws)
            all_raws.extend(raws)
            touched_platforms.update(r.platform for r in raws)
    declared: dict[str, dict[str, int]] = {k: dict(v) for k, v in ((run.stats or {}).get("declared") or {}).items()}
    for col in collectors:
        for slug, n in (getattr(col, "declared", None) or {}).items():
            declared.setdefault(col.platform, {})[slug] = n
    up = upsert_creatives(session, run, all_raws, seen_on, download_media=download_media)
    comp_ids = [c.id for c in session.query(Competitor).filter(Competitor.slug.in_([c.slug for c in configs])).all()]
    inactive = mark_unseen_inactive(session, run, comp_ids, touched_platforms) if touched_platforms else 0
    run.status = "collected"
    run.stats = {**(run.stats or {}), "declared": declared,
                 "collect": {**dict(stats), **dict(up), "marked_inactive": inactive, "errors": errors, "seen_on": seen_on.isoformat()}}
    session.flush()
    return run.stats["collect"]


def import_file(session: Session, period: str, path: str | Path, slug: str | None, platform: str | None, seen_on: dt.date | None = None) -> dict[str, Any]:
    raws = load_manual_file(path, default_slug=slug, default_platform=platform)
    run = get_or_create_run(session, period)
    sync_competitors(session, load_competitors())
    stats = upsert_creatives(session, run, raws, seen_on or dt.date.today(), download_media=True)
    if run.status == "created":
        run.status = "collected"
    return dict(stats)


# ----------------------------------------------------------------------------- classify
def run_creatives(session: Session, run: Run) -> list[Creative]:
    ids = [s.creative_id for s in session.query(Sighting).filter_by(run_id=run.id).all()]
    if not ids:
        return []
    return session.query(Creative).filter(Creative.id.in_(ids)).filter(Creative.qa_status != "rejected").all()


FALLBACK_MARK = "[fallback por error"
FATAL_API_ERRORS = ("credit balance is too low", "could not resolve authentication", "invalid x-api-key", "authentication_error", "permission_error")


def _is_fallback(classification: Classification | None) -> bool:
    return bool(classification) and FALLBACK_MARK in ((classification.data or {}).get("summary") or "")


def _is_fatal_api_error(exc: Exception) -> bool:
    """Errores que se repetirían en cada anuncio (sin crédito, sin clave): conviene frenar, no seguir."""
    msg = str(exc).lower()
    return any(k in msg for k in FATAL_API_ERRORS)


def classify(session: Session, period: str, mock: bool = False, reclassify: bool = False, taxonomy: Taxonomy | None = None, retry_failed: bool = False) -> dict[str, Any]:
    from .ai.client import usage

    taxonomy = taxonomy or load_taxonomy()
    run = get_or_create_run(session, period)
    stats: Counter = Counter()
    model_name = "heuristic" if mock else settings.classify_model
    usage.reset()
    for creative in run_creatives(session, run):
        latest = creative.latest_classification
        if latest and not reclassify and not (retry_failed and _is_fallback(latest)):
            stats["skipped"] += 1
            continue
        try:
            data = heuristic_classify(creative, taxonomy) if mock else classify_creative(creative, taxonomy)
        except Exception as exc:
            if _is_fatal_api_error(exc):
                log.error("clasificación detenida: %s", exc)
                stats["aborted"] = str(exc)[:300]
                break
            log.warning("clasificación falló para creativo %s: %s; usando heurística", creative.id, exc)
            data = heuristic_classify(creative, taxonomy)
            data["summary"] += f" [fallback por error: {type(exc).__name__}]"
            stats["fallback"] += 1
        session.add(Classification(creative_id=creative.id, run_id=run.id, model=model_name, data=data, confidence=data.get("confidence", "medium")))
        stats["classified"] += 1
        if not mock and usage.usd >= settings.max_ai_usd_per_run:
            log.error("clasificación detenida: se llegó al tope de gasto de US$ %.2f (MAX_AI_USD_PER_RUN)", settings.max_ai_usd_per_run)
            stats["aborted"] = f"tope de gasto de US$ {settings.max_ai_usd_per_run:.2f} alcanzado"
            break
    session.flush()
    session.expire_all()
    run.status = "classified"
    if not mock:
        stats["usd"] = round(usage.usd, 3)
    run.stats = {**(run.stats or {}), "classify": dict(stats)}
    return dict(stats)


# ----------------------------------------------------------------------------- analyze
def items_for(session: Session, run: Run | None, taxonomy: Taxonomy) -> list[dict[str, Any]]:
    if run is None:
        return []
    return [creative_to_item(c, taxonomy) for c in run_creatives(session, run)]


def analyze(session: Session, period: str, taxonomy: Taxonomy | None = None) -> dict[str, Any]:
    taxonomy = taxonomy or load_taxonomy()
    run = get_or_create_run(session, period)
    prev = session.query(Run).filter_by(period=previous_period(period)).one_or_none()
    items = items_for(session, run, taxonomy)
    prev_items = items_for(session, prev, taxonomy)
    metrics = build_metrics(items, taxonomy, previous_items=prev_items, period=period)
    metrics["concepts"] = group_concepts([it for it in items if it["tier"] != "own"])
    add_declared_totals(metrics, (run.stats or {}).get("declared") or {})
    run.metrics = metrics
    run.status = "analyzed"
    session.flush()
    return metrics


def add_declared_totals(metrics: dict[str, Any], declared: dict[str, dict[str, int]]) -> None:
    """Agrega a cada ficha cuántos anuncios activos declaran Meta y Google (además de los analizados)."""
    by_slug: dict[str, dict[str, int]] = {}
    for platform, per in declared.items():
        for slug, n in per.items():
            by_slug.setdefault(slug, {})[platform] = n
    market_total = 0
    for p in metrics.get("competitor_profiles") or []:
        d = by_slug.get(p.get("slug"), {})
        p["declared"] = d
        p["declared_total"] = sum(d.values()) if d else None
        market_total += p["declared_total"] or 0
    own = metrics.get("own_profile")
    if own:
        d = by_slug.get(own.get("slug") or "", {})
        own["declared"] = d
        own["declared_total"] = sum(d.values()) if d else None
    metrics.setdefault("totals", {})["declared"] = market_total or None


# ----------------------------------------------------------------------------- synthesize
def synthesize_run(session: Session, period: str, mock: bool = False) -> Synthesis:
    taxonomy = load_taxonomy()
    run = get_or_create_run(session, period)
    if not run.metrics:
        analyze(session, period, taxonomy)
    items = items_for(session, run, taxonomy)
    own = [it for it in items if it["tier"] == "own"]
    prev = session.query(Run).filter_by(period=previous_period(period)).one_or_none()
    concepts = run.metrics.get("concepts", [])
    if mock:
        data = mock_synthesis(run.metrics, items, concepts, period)
        model = "mock"
    else:
        data = synthesize(run.metrics, items, concepts, period, previous_metrics=(prev.metrics if prev else None), own_items=own)
        model = settings.synth_model
    synth = Synthesis(run_id=run.id, model=model, data=data, status="draft")
    session.add(synth)
    run.status = "draft"
    session.flush()
    return synth


# ----------------------------------------------------------------------------- report
def report_context(session: Session, period: str) -> dict[str, Any]:
    taxonomy = load_taxonomy()
    run = get_or_create_run(session, period)
    synth = latest_synthesis(session, run)
    if synth is None:
        raise ValueError("No hay síntesis; correr `synthesize` primero")
    items = items_for(session, run, taxonomy)
    review = {"status": synth.status, "reviewer": synth.reviewer, "notes": synth.review_notes}
    return build_report_context(period, run.metrics, synth.data, items, run.metrics.get("concepts", []), review=review)


def build_report(session: Session, period: str, out_dir: str | Path | None = None) -> dict[str, Path]:
    from .config import REPORTS_DIR

    out_dir = Path(out_dir or Path(REPORTS_DIR) / period)
    ctx = report_context(session, period)
    paths = render_report(ctx, out_dir)
    (out_dir / "metrics.json").write_text(json.dumps(ctx["metrics"], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "synthesis.json").write_text(json.dumps(ctx["synthesis"], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return paths


# ----------------------------------------------------------------------------- publish
def publish(session: Session, period: str, force: bool = False) -> dict[str, Any]:
    from .db import Publication

    run = get_or_create_run(session, period)
    synth = latest_synthesis(session, run)
    if synth is None or (synth.status != "approved" and not force):
        raise PermissionError("La síntesis no está aprobada. Correr `review approve` (o --force para saltar el checkpoint).")
    ctx = report_context(session, period)
    env = _env()
    result = publish_to_basecamp(ctx, lambda c: env.get_template("basecamp.html.j2").render(**c))
    session.add(Publication(run_id=run.id, channel="basecamp", external_url=result["document_url"], payload=result))
    run.status = "published"
    run.finished_at = dt.datetime.now(dt.timezone.utc)
    session.flush()
    notify("published", f"Reporte {period} publicado en Basecamp: {result['document_url']}", result)
    return result


# ----------------------------------------------------------------------------- cycle
def run_cycle(
    session: Session,
    period: str,
    mock_ai: bool = False,
    skip_collect: bool = False,
    sources: list[str] | None = None,
    out_dir: str | Path | None = None,
    send_notifications: bool = True,
) -> dict[str, Any]:
    """Todo hasta el borrador. Se detiene ANTES de publicar: ahí entra la validación humana."""
    summary: dict[str, Any] = {"period": period}
    if not skip_collect:
        summary["collect"] = collect(session, period, sources=sources)
    summary["classify"] = classify(session, period, mock=mock_ai)
    metrics = analyze(session, period)
    summary["totals"] = metrics["totals"]
    synthesize_run(session, period, mock=mock_ai)
    paths = build_report(session, period, out_dir)
    summary["report"] = {k: str(v) for k, v in paths.items()}
    from .review import data_qa_queue

    run = get_or_create_run(session, period)
    qa = data_qa_queue(session, run)
    summary["data_qa_pending"] = len(qa)
    if send_notifications:
        notify(
            "draft_ready",
            f"Borrador {period} listo: {metrics['totals']['creatives']} creativos, {len(qa)} para Data QA. "
            f"Revisar con `creative-intel review show --period {period}` y aprobar para publicar en Basecamp.",
            {"period": period, "report": summary["report"], "data_qa_pending": len(qa)},
        )
    return summary
