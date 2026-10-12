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


def currency_symbol(code: str | None) -> str:
    """Moneda de la cuenta publicitaria tal como se escribe en Uruguay (UYU → $, USD → US$)."""
    return {"UYU": "$", "USD": "US$", "": "US$", None: "US$"}.get(code, code or "US$")


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


def _past_edition_sources(configs: list, edition: EventEdition, per_brand: int, errors: list[str],
                          event_keywords: list[str] | None = None, search: dict | None = None) -> tuple[list[RawCreative], list[dict[str, Any]]]:
    from .collectors.google_web import GoogleTransparencyWebCollector
    from .collectors.meta_insights import MetaInsightsCollector

    raws: list[RawCreative] = []
    google = GoogleTransparencyWebCollector(event_keywords=event_keywords)
    for c in configs:
        if not c.active or c.tier not in ("own", "local") or not google.supports(c):
            continue
        for country in (c.ad_countries or [c.country]):
            try:
                raws += google.collect_window(c, country, edition.date_from, edition.date_to, per_brand)
            except Exception as exc:
                errors.append(f"google {c.slug}: {exc}")
    if search is not None:
        search.update(google.window_stats)
    daily: list[dict[str, Any]] = []
    own = next((c for c in configs if c.is_own), None)
    insights = MetaInsightsCollector()
    if own and insights.supports(own):
        try:
            found = insights.collect_window(own, own.country, edition.date_from, edition.date_to, 15)
            print(f"[meta] {own.name}: {len(found)} anuncios propios con inversión entre {edition.date_from} y {edition.date_to}")
            raws += found
        except Exception as exc:
            errors.append(f"anuncios propios (Meta): {exc}")
        try:
            daily = insights.daily(own, edition.date_from, edition.date_to)
        except Exception as exc:
            errors.append(f"resultados diarios (Meta): {exc}")
    elif own:
        errors.append(f"anuncios propios: falta META_ACCESS_TOKEN en .env o meta_ad_account_id de {own.name}")
    return raws, daily


def _organic(configs: list, edition: EventEdition, keywords: list[str], errors: list[str],
             archive: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Instagram orgánico de la marca y de los competidores locales en la ventana del evento.

    Cuentas del cliente: todas las publicaciones de la ventana (sirve para ediciones pasadas). Competencia: si Meta
    no habilita Business Discovery, el perfil público (últimas ~12) más lo que el Radar archivó en corridas anteriores
    de este evento: por eso conviene archivar cada 2-3 días antes y durante."""
    import time

    from .collectors.ig_organic import InstagramOrganic, merge_archive, raw_posts, summarize

    if not any(c.instagram for c in configs):
        return {}
    ig = InstagramOrganic()
    out: dict[str, Any] = {}
    public_fails = 0
    for c in configs:
        if not c.active or not c.instagram or c.tier not in ("own", "local"):
            continue
        try:
            prof = ig.profile(c.instagram, since=edition.date_from, until=edition.date_to)
        except Exception as exc:
            errors.append(f"instagram @{c.instagram}: {str(exc)[:200]}")
            if "token" in str(exc).lower() or "vinculada" in str(exc):
                break  # problema del token: no tiene sentido seguir con las demás cuentas
            public_fails += "público" in str(exc)
            if public_fails >= 3:
                errors.append("instagram: el perfil público no responde (Instagram limita las consultas); se reintenta en la próxima corrida")
                break
            prof = {"username": c.instagram, "posts": [], "source": "archivo"}
        prof = merge_archive(prof, (archive or {}).get(c.slug) or [], edition.date_from, edition.date_to)
        if not prof["posts"] and prof.get("source") == "archivo":
            continue
        out[c.slug] = {"brand": c.name, "own": c.is_own, "raw": raw_posts(prof),
                       **summarize(prof, keywords, assets=settings.assets_dir / "organic")}
        how = {"perfil_publico": f" (perfil público: se ve desde {prof.get('visible_since') or '—'})", "archivo": " (solo archivo)",
               "apify": " (Apify" + (f", tope de {settings.apify_max_posts} alcanzado: llegó hasta {prof.get('visible_since')}" if prof.get("capped") else "")
                        + ")"}.get(prof.get("source"), "")
        print(f"[instagram] @{c.instagram}: {out[c.slug]['posts']} publicaciones entre {edition.date_from} y {edition.date_to}"
              + how + (f", {prof['archived']} del archivo del Radar" if prof.get("archived") else ""))
        if prof.get("source") == "perfil_publico":
            time.sleep(2)  # sin apuro: Instagram corta si se le pregunta muy seguido
    return out


def _organic_archive(session: Session, event: EventConfig) -> dict[str, list[dict[str, Any]]]:
    """Todas las publicaciones de Instagram guardadas en corridas anteriores de este evento, por marca."""
    from .db import Run

    out: dict[str, dict[str, dict[str, Any]]] = {}
    for run in session.query(Run).filter(Run.period.like(f"ev-{event.key}-%")).all():
        for slug, o in ((run.stats or {}).get("organic") or {}).items():
            for p in o.get("raw") or []:
                if p.get("id"):
                    out.setdefault(slug, {})[p["id"]] = p
    return {slug: list(posts.values()) for slug, posts in out.items()}


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
    own_daily: list[dict[str, Any]] = []
    market_search: dict[str, Any] = {}
    if not live and collector is None:
        # Edición pasada: Meta ya no muestra esos anuncios (fuera de la UE), pero Google guarda el historial
        # y la cuenta publicitaria del cliente tiene sus resultados reales de esas fechas.
        extra, own_daily = _past_edition_sources(configs, edition, per_brand, errors, event.keywords, market_search)
        raws += extra
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
    organic = _organic(configs, edition, event.keywords, errors, _organic_archive(session, event)) if collector is None else {}
    _register_market_advertisers(session, raws)
    up = upsert_creatives(session, run, raws, dt.date.today(), download_media=True)
    run.status = "collected"
    run.stats = {**(run.stats or {}), "collect": {"ads": len(raws), **dict(up), "errors": errors}}
    if own_daily:
        run.stats = {**run.stats, "own_daily": own_daily}
    if market_search:
        run.stats = {**run.stats, "market_search": market_search}
    if organic:
        run.stats = {**run.stats, "organic": organic}
    session.flush()
    return run.stats["collect"]


def event_related(items: list[dict[str, Any]], keywords: list[str], date_from: dt.date | None) -> tuple[list[dict[str, Any]], int]:
    """De la competencia, solo lo que tiene que ver con el evento: anuncios que lo nombran o que arrancaron dentro
    de la ventana. La publicidad de siempre (ej. una campaña de marca al aire hace 250 días) no es del evento."""
    from .analysis.metrics import start_known
    from .relevance import item_text, matches

    keep = []
    for it in items:
        names_it = matches(item_text(it), keywords)
        started_in = bool(date_from and start_known(it) and it.get("first_seen") and it["first_seen"] >= date_from.isoformat())
        if names_it or started_in:
            keep.append(it)
    return keep, len(items) - len(keep)


def _gemini_ideas(plan: dict[str, Any], event: EventConfig, metrics: dict[str, Any], items: list[dict[str, Any]], client: Any) -> dict[str, Any]:
    """Las ideas y guiones los escribe Gemini (si está configurado) sobre los hechos ya armados."""
    from .ai.creative import strip_for, write_ideas
    from .ai.synthesize import _compact_items

    context = {
        "evento": event.name,
        "hechos": plan.get("summary"),
        "oferta_recomendada": plan.get("offer_recommendation"),
        "calendario": plan.get("calendar"),
        "no_hacer": plan.get("avoid"),
        "resultados_propios_edicion": metrics.get("own_results"),
        "instagram_organico": [{k: o.get(k) for k in ("brand", "own", "followers", "posts", "avg_likes", "avg_comments", "avg_rate", "event_posts", "types", "top")}
                               for o in metrics.get("organic") or []],
        "resultados_propios_por_momento": (metrics.get("own_daily") or {}).get("phases"),
        "anuncios_del_mercado": _compact_items([it for it in items if it.get("tier") != "own"], limit=40),
        "anuncios_propios": _compact_items([it for it in items if it.get("tier") == "own"], limit=20),
    }
    base = plan.get("ideas") or []
    if base:  # el razonamiento (dato, lectura, hipótesis, prueba) queda del análisis; Gemini reescribe la pieza
        context["ideas_a_reescribir"] = [{k: i.get(k) for k in ("title", "format", "angle", "evidence", "reading", "hypothesis", "hook_line", "copy_draft")}
                                         for i in base]
        ask = (f"Reescribí estas {len(base)} ideas para {event.name} (for_what = el título original, tal cual): misma idea y "
               "mismo dato de origen, con mejor gancho, guion, copy y dirección visual.")
    else:
        ask = (f"Escribí 4 ideas para {event.name}, una para cada momento clave del calendario "
               "(for_what = el momento). Cada una con un producto distinto de la marca.")
    ideas = write_ideas(client, context, ask)
    if not ideas:
        return plan
    if base:
        by_title = {i.get("for_what", "").strip().lower(): i for i in ideas}
        merged = []
        for n, b in enumerate(base):
            g = by_title.get((b.get("title") or "").strip().lower()) or (ideas[n] if n < len(ideas) else None)
            merged.append({**b, **{k: v for k, v in strip_for(g).items() if v}} if g else b)
        return {**plan, "ideas": merged, "ideas_by": "Gemini (piezas) sobre el análisis"}
    return {**plan, "ideas": [strip_for(i) | {"moment": i.get("for_what", "")} for i in ideas], "ideas_by": "Gemini"}


def _organic_for_report(organic: dict[str, Any], out: Path) -> list[dict[str, Any]]:
    """Las cuentas ordenadas (el cliente primero) con sus imágenes copiadas junto al informe."""
    import shutil

    rows = []
    for slug, o in organic.items():
        o = {**{k: v for k, v in o.items() if k != "raw"}, "top": [dict(p) for p in o.get("top") or []]}
        for p in o["top"]:
            img = p.get("image")
            if img and Path(img).is_file():
                (out / "assets").mkdir(parents=True, exist_ok=True)
                shutil.copy(img, out / "assets" / Path(img).name)
                p["image_rel"] = f"assets/{Path(img).name}"
        rows.append(o)
    return sorted(rows, key=lambda o: (not o.get("own"), -(o.get("avg_rate") or 0)))


def run_event(session: Session, event: EventConfig, edition: EventEdition, mock_ai: bool = False, skip_collect: bool = False,
              out_dir: str | Path | None = None) -> dict[str, Any]:
    from . import pipeline
    from .ai.client import check_api
    from .ai.event import mock_event_plan, synthesize_event
    from .analysis.event import analyze_event, own_daily_summary, own_results, own_results_groups
    from .analysis.metrics import start_known
    from .analysis.signals import signal_list
    from .config import REPORTS_DIR, load_brand
    from .report.build import _env, render_assets, top_creatives

    if not mock_ai:
        check_api()
    summary: dict[str, Any] = {"event": event.name, "edition": edition.label}
    period = event_period(event, edition)
    own_cfg = next((c for c in load_competitors() if c.is_own), None)
    if not skip_collect and not edition.label.startswith("vivo-") and own_cfg and own_cfg.meta_ad_account_id:
        from .meta_token import token_problem

        problem = token_problem(settings.meta_access_token)
        if problem:  # sin token no hay resultados propios: se corta acá, antes de gastar tiempo e IA
            raise pipeline.NoData(problem)
    if not skip_collect:
        pipeline.stage("Buscando los anuncios y los resultados")
        summary["collect"] = collect_event(session, event, edition)
        session.commit()
    pipeline.stage("Clasificando los anuncios")
    summary["classify"] = pipeline.classify(session, period, mock=mock_ai)
    session.commit()
    taxonomy = load_taxonomy()
    client = load_client()
    run = get_or_create_run(session, period)
    items = pipeline.items_for(session, run, taxonomy)
    if not items and not (run.stats or {}).get("own_daily") and not (run.stats or {}).get("organic"):
        stats = (summary.get("collect") or {})
        errs = "; ".join(stats.get("errors") or [])[:600]
        raise pipeline.NoData(f"No se encontraron anuncios de {event.name} {edition.label}. "
                              + (f"Errores al recolectar: {errs}. " if errs else "")
                              + ("" if edition.label.startswith("vivo-") else NO_ADS_HELP))
    for it in items:
        it["signals"] = signal_list(it, start_known(it))
    out = Path(out_dir or Path(REPORTS_DIR) / "eventos" / f"{event.key}-{edition.label}")
    own_items = [it for it in items if it.get("tier") == "own"]
    market_items, dropped = event_related([it for it in items if it.get("tier") != "own"], event.keywords, edition.date_from)
    if dropped:
        print(f"[evento] {dropped} anuncios de la competencia quedaron afuera: estaban al aire desde antes y no nombran {event.name}")
    items = own_items + market_items
    render_assets(own_items, out)  # vista previa de los anuncios propios
    # "Cómo lo encaró el mercado" se calcula SOLO con la competencia; lo del cliente va aparte, con sus resultados
    pipeline.stage("Analizando los datos")
    metrics = analyze_event(market_items, edition.start, edition.end, own_name=client.name)
    metrics["own_ads"] = len(own_items)  # todos los propios (también los de la Biblioteca, sin resultados)
    metrics["own_daily"] = own_daily_summary((run.stats or {}).get("own_daily") or [], edition.start, edition.end)
    metrics["own_results"] = own_results(own_items)
    metrics["own_groups"] = own_results_groups(metrics["own_results"])
    metrics["market_search"] = (run.stats or {}).get("market_search") or {}
    metrics["currency"] = currency_symbol(next((d.get("currency") for d in (run.stats or {}).get("own_daily") or [] if d.get("currency")), None)
                                          or next(((it.get("engagement") or {}).get("performance", {}).get("currency") for it in own_items
                                                   if ((it.get("engagement") or {}).get("performance") or {}).get("currency")), None))
    metrics["collect_errors"] = ((run.stats or {}).get("collect") or {}).get("errors") or []
    metrics["organic"] = _organic_for_report((run.stats or {}).get("organic") or {}, out)
    from .ai.synthesize import sample_note
    from .analysis.data_coverage import event_coverage

    metrics["data_coverage"] = event_coverage(metrics, live=edition.label.startswith("vivo-"))
    metrics["sample"] = sample_note(items, 150)  # qué anuncios vio la IA
    next_dates = {"start": event.next_start.isoformat() if event.next_start else None,
                  "end": event.next_end.isoformat() if event.next_end else None}
    if mock_ai:
        plan = mock_event_plan(event.name, metrics, client.name)
    else:
        labels = {k: taxonomy.label(k) for k in taxonomy.labels} if getattr(taxonomy, "labels", None) else {}
        pipeline.stage("Escribiendo la guía con IA")
        plan = synthesize_event(event.name, metrics, items, client, next_dates, labels=labels)
        plan = _gemini_ideas(plan, event, metrics, items, client)
    run.metrics = {"event": metrics, "plan": plan}
    run.status = "draft"
    session.flush()

    pipeline.stage("Armando el informe")
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
