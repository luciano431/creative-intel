"""Informe "Qué hicimos": la pauta propia del cliente año por año (solo pauta de Meta, nada orgánico).

Junta, para cada año pedido, lo que dice la cuenta publicitaria del cliente: el total del año, el mismo tramo del año
(1 de enero a la fecha de hoy, para comparar parejo con el año en curso), mes por mes, las campañas, los anuncios con
más inversión y con mejor costo por resultado (con su imagen y su texto), lo que gastó sin resultados y cada fecha
comercial (con su previa). Con eso el modelo de análisis dice qué funcionó, qué repetir, qué iterar y qué cortar, y
arma el plan; Gemini escribe las piezas del plan.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import re
import shutil
from pathlib import Path
from typing import Any

from .config import settings

TYPE_ORDER = ["compras", "conversaciones", "leads", "clics al sitio"]
PREVIA = 10


def _nth_sunday(year: int, month: int, n: int) -> dt.date:
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def _black_friday(year: int) -> dt.date:
    nov1 = dt.date(year, 11, 1)
    thursday = nov1 + dt.timedelta(days=(3 - nov1.weekday()) % 7 + 21)
    return thursday + dt.timedelta(days=1)


# Fechas que se calculan solas para cualquier año (Uruguay). El resto sale de las ediciones de events.yaml.
RULES = {
    "black-friday": lambda y: (_black_friday(y), _black_friday(y)),
    "navidad": lambda y: (dt.date(y, 12, 24), dt.date(y, 12, 25)),
    "reyes": lambda y: (dt.date(y, 1, 6), dt.date(y, 1, 6)),
    "san-valentin": lambda y: (dt.date(y, 2, 14), dt.date(y, 2, 14)),
    "dia-de-la-madre": lambda y: (_nth_sunday(y, 5, 2),) * 2,
    "dia-del-padre": lambda y: (_nth_sunday(y, 7, 2),) * 2,
    "dia-del-nino": lambda y: (_nth_sunday(y, 8, 2),) * 2,
}


def event_windows(years: list[int], today: dt.date, previa: int = PREVIA) -> list[dict[str, Any]]:
    """Cada fecha comercial de esos años que ya pasó: sus días y la previa."""
    from .config import load_events

    out: dict[tuple[str, dt.date], dict[str, Any]] = {}
    for key, ev in load_events().items():
        dates = [(e.start, e.end or e.start) for e in ev.editions if e.start]
        if key in RULES:
            dates += [RULES[key](y) for y in years]
        for start, end in dates:
            if start.year in years and end < today:
                out[(key, start)] = {"key": key, "name": ev.name, "year": start.year, "start": start, "end": end,
                                     "from": start - dt.timedelta(days=previa)}
    return sorted(out.values(), key=lambda w: w["start"])


def _values(r: dict[str, Any]) -> dict[str, Any]:
    from .collectors.meta_insights import results_by_type

    vals = {a.get("action_type"): float(a.get("value") or 0) for a in r.get("action_values") or []}
    return {"spend": float(r.get("spend") or 0), "impressions": int(float(r.get("impressions") or 0)),
            "clicks": int(float(r.get("clicks") or 0)), "by_type": results_by_type(r.get("actions")),
            "revenue": vals.get("purchase") or vals.get("offsite_conversion.fb_pixel_purchase") or 0.0,
            "currency": r.get("account_currency") or ""}


def totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Suma de filas de Meta: inversión, CTR y cada tipo de resultado con su costo (los tipos no se suman entre sí)."""
    spend = impressions = clicks = revenue = 0.0
    types: dict[str, float] = {}
    currency = ""
    for r in rows:
        v = _values(r)
        spend += v["spend"]
        impressions += v["impressions"]
        clicks += v["clicks"]
        revenue += v["revenue"]
        currency = currency or v["currency"]
        for k, n in v["by_type"].items():
            types[k] = types.get(k, 0) + n
    return {"spend": round(spend, 2), "impressions": int(impressions), "clicks": int(clicks),
            "ctr": round(100 * clicks / impressions, 2) if impressions else None,
            "by_type": {k: {"total": round(types[k]), "cost": round(spend / types[k], 2) if types[k] else None}
                        for k in TYPE_ORDER if types.get(k)},
            "roas": round(revenue / spend, 2) if revenue and spend else None, "currency": currency}


def _ad_row(r: dict[str, Any]) -> dict[str, Any]:
    from .collectors.meta_insights import performance_row

    p = performance_row(r)
    return {"ad_id": str(r.get("ad_id")), "ad_name": r.get("ad_name") or "", "campaign": r.get("campaign_name") or "",
            "adset": r.get("adset_name") or "", **{k: p[k] for k in ("spend", "impressions", "clicks", "ctr", "result_type", "results",
                                                                       "cost_per_result", "stage", "objective", "roas", "by_type")}}


def _name_key(name: str) -> str:
    """Nombre del anuncio sin números de versión ni fechas, para ver qué se repitió de un año a otro."""
    s = re.sub(r"\b(20\d\d|v\d+|copia|copy|\d{1,2}[/-]\d{1,2})\b", " ", (name or "").lower())
    return re.sub(r"[^a-záéíóúñü]+", " ", s).strip()


def pick_ads(ads: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Los anuncios del año que vale la pena mirar: los de más inversión, los de mejor costo por compra, los de mejor
    costo por conversación o lead, y los que gastaron sin traer el resultado que buscaban."""
    by_spend = sorted(ads, key=lambda a: -a["spend"])
    buyers = [a for a in ads if (a["by_type"].get("compras") or 0) >= 2]
    best_buy = sorted(buyers, key=lambda a: a["spend"] / a["by_type"]["compras"])[:6]
    convo = [a for a in ads if a["result_type"] in ("conversaciones", "leads") and (a["results"] or 0) >= 3 and a["cost_per_result"]]
    best_conv = sorted(convo, key=lambda a: a["cost_per_result"])[:4]
    median = sorted(a["spend"] for a in ads)[len(ads) // 2] if ads else 0
    wasted = [a for a in by_spend if a["stage"] == "bofu" and not a["results"] and a["spend"] >= max(median, 1)][:5]
    return {"top_spend": by_spend[:8], "best_purchase": best_buy, "best_conversation": best_conv, "no_results": wasted}


def collect_history(own: Any, years: list[int], today: dt.date, errors: list[str], max_event_ads: int = 10) -> dict[str, Any]:
    """Todo lo que la cuenta del cliente dice de esos años (consulta a Meta; las imágenes y textos quedan guardados)."""
    from .collectors.meta_insights import MetaInsightsCollector

    col = MetaInsightsCollector()
    data: dict[str, Any] = {"years": {}, "events": [], "today": today.isoformat()}
    keep = ("date_start", "spend", "impressions", "clicks", "actions", "action_values", "account_currency")
    daily_all: list[dict[str, Any]] = []
    for y in years:
        since, until = dt.date(y, 1, 1), min(dt.date(y, 12, 31), today)
        if since > today:
            continue
        ytd_until = min(dt.date(y, today.month, min(today.day, calendar.monthrange(y, today.month)[1])), until)
        print(f"[historial] {y}: pauta de {since} a {until}")
        try:
            # Día por día: de ahí salen el año, el mismo tramo, los meses y cada fecha comercial (una sola consulta)
            daily = [{k: r.get(k) for k in keep} for r in col.insights(own, since, until, increment="1")]
            campaigns = col.insights(own, since, until, level="campaign")
            ads = [_ad_row(r) for r in col.insights(own, since, until, level="ad") if float(r.get("spend") or 0) > 0]
        except RuntimeError as exc:
            if "access token" in str(exc).lower():
                raise
            errors.append(f"{y}: {exc}")
            continue
        daily_all += daily
        months = [{"month": m, **totals([r for r in daily if (r.get("date_start") or "")[:7] == m])}
                  for m in sorted({(r.get("date_start") or "")[:7] for r in daily if r.get("date_start")})]
        camp_rows = []
        for r in campaigns:
            t = totals([r])
            if t["spend"]:
                camp_rows.append({"name": r.get("campaign_name") or "", "objective": r.get("objective") or "", **t})
        stages: dict[str, float] = {}
        for a in ads:
            stages[a["stage"] or "sin dato"] = stages.get(a["stage"] or "sin dato", 0) + a["spend"]
        data["years"][str(y)] = {
            "since": since.isoformat(), "until": until.isoformat(), "ytd_until": ytd_until.isoformat(),
            "total": totals(daily), "ytd": totals([r for r in daily if (r.get("date_start") or "") <= ytd_until.isoformat()]),
            "months": months, "campaigns": sorted(camp_rows, key=lambda c: -c["spend"])[:15], "campaign_count": len(camp_rows),
            "ads_with_spend": len(ads), "stage_spend": {k: round(v, 2) for k, v in sorted(stages.items(), key=lambda kv: -kv[1])},
            "picks": pick_ads(ads), "names": sorted({_name_key(a["ad_name"]) for a in ads if _name_key(a["ad_name"])}),
        }
    for w in event_windows(years, today):
        t = totals([r for r in daily_all if w["from"].isoformat() <= (r.get("date_start") or "") <= w["end"].isoformat()])
        data["events"].append({**{k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in w.items()}, **t, "top": []})
    # Los anuncios de las fechas con más inversión (una consulta por fecha, solo las que importan)
    for e in sorted((e for e in data["events"] if e["spend"]), key=lambda e: -e["spend"])[:max_event_ads]:
        try:
            rows = col.insights(own, dt.date.fromisoformat(e["from"]), dt.date.fromisoformat(e["end"]), level="ad")
        except RuntimeError as exc:
            if "access token" in str(exc).lower():
                raise
            errors.append(f"{e['name']} {e['year']}: {exc}")
            continue
        e["top"] = [a for a in sorted((_ad_row(r) for r in rows), key=lambda a: -a["spend"])[:4] if a["spend"] > 0]
        print(f"[historial] {e['name']} {e['start'][:7]}: {e['spend']} de inversión, {len(e['top'])} anuncios")
    # Imagen, texto y formato de cada anuncio que se muestra (guardados: la segunda corrida no consulta a Meta)
    shown = {a["ad_id"] for yd in data["years"].values() for lst in yd["picks"].values() for a in lst}
    shown |= {a["ad_id"] for e in data["events"] for a in e["top"]}
    print(f"[historial] {len(shown)} anuncios para ver con su imagen y su texto")
    details = {}
    for ad_id in sorted(shown):
        try:
            details[ad_id] = col.ad_details(ad_id)
        except RuntimeError as exc:
            errors.append(f"anuncio {ad_id}: {exc}")
    data["details"] = details
    return data


def collect_competition(today: dt.date, errors: list[str], per_brand: int = 8, collector: Any = None) -> list[dict[str, Any]]:
    """Lo que la competencia tiene corriendo HOY en la Biblioteca de anuncios de Meta (solo pauta, solo activos).

    Meta no muestra inversión ni resultados de otras marcas: lo que se puede leer es qué dicen, con qué formato, desde
    cuándo están al aire y si tienen varias versiones. Un anuncio que lleva semanas prendido es señal de que les rinde."""
    from .collectors.meta_web import MetaAdLibraryWebCollector
    from .config import load_competitors

    col = collector or MetaAdLibraryWebCollector()
    out = []
    for c in load_competitors():
        if not c.active or c.is_own or c.tier != "local" or not c.meta_page_id:
            continue
        ads: list[dict[str, Any]] = []
        for country in (c.ad_countries or [c.country]):
            try:
                raws = col.collect_window(c, country, None, None, per_brand)
            except Exception as exc:
                errors.append(f"competencia {c.name}: {str(exc)[:200]}")
                continue
            for r in raws:
                if not r.is_active:
                    continue
                days = (today - r.first_seen).days if r.first_seen else None
                ads.append({"id": r.external_id, "format": r.format, "headline": r.headline[:160], "body": r.body_text[:500],
                            "cta": r.cta_text, "landing": r.landing_url, "since": r.first_seen.isoformat() if r.first_seen else None,
                            "days_running": days, "versions": bool((r.raw or {}).get("multiple_versions")),
                            "image": (r.raw or {}).get("screenshot") or "", "url": r.snapshot_url})
        ads.sort(key=lambda a: -(a["days_running"] or 0))
        out.append({"slug": c.slug, "name": c.name, "ads": ads, "long_running": sum(1 for a in ads if (a["days_running"] or 0) >= 30),
                    "formats": {f: sum(1 for a in ads if a["format"] == f) for f in sorted({a["format"] for a in ads})}})
        print(f"[historial] competencia {c.name}: {len(ads)} anuncios activos hoy")
    return out


def repeated_names(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Nombres de anuncio (sin versión ni fecha) que aparecen en más de un año."""
    seen: dict[str, list[str]] = {}
    for y, yd in data["years"].items():
        for n in yd.get("names") or []:
            if len(n) >= 4:
                seen.setdefault(n, []).append(y)
    return [{"name": n, "years": ys} for n, ys in sorted(seen.items()) if len(ys) > 1][:30]


def history_metrics(data: dict[str, Any]) -> dict[str, Any]:
    """Lo que ven el modelo y el informe: los datos con el detalle de cada anuncio pegado."""
    det = data.get("details") or {}

    def full(a: dict[str, Any]) -> dict[str, Any]:
        d = det.get(a["ad_id"]) or {}
        return {**a, **{k: d.get(k) for k in ("title", "body", "format", "created", "image")}}

    years = {}
    for y, yd in data["years"].items():
        years[y] = {**{k: v for k, v in yd.items() if k not in ("picks", "names")},
                    "picks": {k: [full(a) for a in lst] for k, lst in yd["picks"].items()}}
    events = [{**e, "top": [full(a) for a in e.get("top") or []]} for e in data.get("events") or []]
    currency = next((yd["total"]["currency"] for yd in data["years"].values() if yd["total"].get("currency")), "")
    types = [t for t in TYPE_ORDER if any(t in (yd["total"].get("by_type") or {}) for yd in data["years"].values())]
    return {"years": years, "events": events, "repeated": repeated_names(data), "currency": currency, "types": types,
            "today": data.get("today"), "competition": data.get("competition"),
            "competition_date": data.get("competition_date")}


def coverage(metrics: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    rows = []
    for y, yd in metrics["years"].items():
        t = yd["total"]
        rows.append({"ok": bool(t["spend"]), "source": f"Pauta {y}",
                     "detail": (f"{yd['ads_with_spend']} anuncios con inversión en {yd['campaign_count']} campañas, del {yd['since']} al {yd['until']}"
                                if t["spend"] else "sin inversión registrada en la cuenta")})
    rows.append({"ok": bool(metrics["events"]), "source": "Fechas comerciales",
                 "detail": f"{len(metrics['events'])} fechas con su previa de {PREVIA} días" if metrics["events"] else "ninguna fecha cargada en esos años"})
    comp = metrics.get("competition")
    if comp is not None:
        n = sum(len(c["ads"]) for c in comp)
        rows.append({"ok": bool(n), "source": "Competencia hoy",
                     "detail": f"{n} anuncios activos hoy de {sum(1 for c in comp if c['ads'])} de {len(comp)} marcas (Biblioteca de Meta; sin inversión ni resultados, que Meta no muestra)"
                     if comp else "no hay competidores locales cargados"})
    rows.append({"ok": True, "source": "Orgánico", "detail": "no entra en este informe (solo pauta)"})
    missing = sum(1 for r in rows if not r["ok"])
    return {"level": "alta" if not missing and not errors else "media" if missing <= 1 else "baja", "rows": rows, "errors": errors}


def _copy_images(metrics: dict[str, Any], out: Path) -> None:
    lists = [lst for yd in metrics["years"].values() for lst in yd["picks"].values()] + [e["top"] for e in metrics["events"]] \
        + [c["ads"] for c in metrics.get("competition") or []]
    for lst in lists:
        for a in lst:
            img = a.get("image")
            if img and Path(img).is_file():
                (out / "assets").mkdir(parents=True, exist_ok=True)
                shutil.copy(img, out / "assets" / Path(img).name)
                a["image_rel"] = f"assets/{Path(img).name}"


def _for_ai(metrics: dict[str, Any]) -> dict[str, Any]:
    """Los datos sin rutas de imágenes (no le sirven al modelo)."""
    def clean(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items() if k not in ("image", "image_rel")}
        if isinstance(x, list):
            return [clean(v) for v in x]
        return x
    return clean(metrics)


def upcoming(today: dt.date, months: int = 4) -> list[dict[str, str]]:
    """Las fechas comerciales de los próximos meses (para el plan)."""
    from .config import load_events

    horizon = today + dt.timedelta(days=30 * months)
    out = []
    for key, ev in load_events().items():
        cands = [(ev.next_start, ev.next_end)] + [RULES[key](y) for y in (today.year, today.year + 1)] if key in RULES else [(ev.next_start, ev.next_end)]
        for s, e in cands:
            if s and today <= s <= horizon:
                out.append({"name": ev.name, "start": s.isoformat(), "end": (e or s).isoformat()})
                break
    return sorted(out, key=lambda x: x["start"])


def gemini_pieces(plan: dict[str, Any], metrics: dict[str, Any], client: Any) -> dict[str, Any]:
    """Gemini escribe cada pieza del plan (título, qué se ve, copy) sobre el brief del análisis. Si falla, lo dice."""
    from .ai.creative import GeminiFailed, write_plan_pieces

    items = plan.get("plan") or []
    if not items:
        return plan
    best = [a for yd in metrics["years"].values() for a in yd["picks"]["best_purchase"][:3] + yd["picks"]["best_conversation"][:2]]
    context = {"aprendizajes": plan.get("learnings"), "no_hacer": plan.get("avoid"), "moneda": metrics.get("currency"),
               "competencia_hoy": plan.get("competition_summary"),
               "anuncios_propios_que_mejor_funcionaron": [{k: a.get(k) for k in ("ad_name", "title", "body", "format", "result_type", "results", "cost_per_result")}
                                                          for a in best[:10]],
               "formato": "Piezas estáticas (imagen, carrusel o catálogo), no video, salvo que el brief diga video."}
    briefs = [{"title": i.get("title"), "when": i.get("when"), "format": i.get("format"), "product": i.get("product"),
               "angle": i.get("angle"), "what_to_do": [i.get("brief")], "evidence": i.get("evidence")} for i in items]
    try:
        pieces = write_plan_pieces(client, context, briefs)
    except GeminiFailed as exc:
        print(f"[gemini] no escribió las piezas: {exc}")
        return {**plan, "creative_error": str(exc)}
    by_title = {(x.get("for_what") or "").strip().lower(): x for x in pieces}
    merged = []
    for n, i in enumerate(items):
        g = by_title.get((i.get("title") or "").strip().lower()) or (pieces[n] if n < len(pieces) else None)
        merged.append({**i, "brief_title": i.get("title"), **({"piece_title": g.get("title"), "what_to_do": g.get("what_to_do"),
                                                               "copy_lines": g.get("copy_lines")} if g else {})})
    return {**plan, "plan": merged, "ideas_by": "Gemini"}


def run_history(own: Any, years: list[int], mock_ai: bool = False, reuse: bool = False, out_dir: str | Path | None = None,
                today: dt.date | None = None, competition: bool = True, competition_collector: Any = None) -> dict[str, Any]:
    from . import pipeline
    from .config import REPORTS_DIR, load_brand, load_client
    from .report.build import _env

    today = today or dt.date.today()
    years = sorted(set(years))
    out = Path(out_dir or Path(REPORTS_DIR) / "historial" / f"{years[0]}-{years[-1]}")
    out.mkdir(parents=True, exist_ok=True)
    saved = out / "datos.json"
    if not mock_ai:
        from .ai.client import check_api
        from .ai.creative import check_gemini

        check_api()
        problem = check_gemini()
        if problem:
            raise pipeline.NoData(f"Gemini no anda ({problem}; modelo: {settings.gemini_creative_model}). Las piezas del plan "
                                  "las escribe Gemini: revisá GEMINI_API_KEY y GEMINI_CREATIVE_MODEL en .env (radar check-api).")
    errors: list[str] = []
    if reuse and saved.exists():
        stored = json.loads(saved.read_text(encoding="utf-8"))
        data, errors = stored["data"], stored.get("errors") or []
        print(f"[historial] uso los datos ya bajados ({saved})")
    else:
        if not own or not own.meta_ad_account_id:
            raise pipeline.NoData("El cliente no tiene cuenta publicitaria cargada (meta_ad_account_id en competitors.yaml, sección own).")
        from .meta_token import token_problem

        problem = token_problem(settings.meta_access_token)
        if problem:
            raise pipeline.NoData(problem)
        pipeline.stage("Bajando la pauta de cada año")
        data = collect_history(own, years, today, errors)
    if competition and data.get("competition") is None:
        pipeline.stage("Mirando qué tiene corriendo hoy la competencia")
        data["competition"] = collect_competition(today, errors, collector=competition_collector)
        data["competition_date"] = today.isoformat()
    saved.write_text(json.dumps({"data": data, "errors": errors}, ensure_ascii=False, default=str), encoding="utf-8")
    if not any(yd["total"]["spend"] for yd in data["years"].values()):
        raise pipeline.NoData(f"La cuenta no tiene inversión registrada entre {years[0]} y {years[-1]}." + (f" Errores: {'; '.join(errors)[:400]}" if errors else ""))
    metrics = history_metrics(data)
    metrics["data_coverage"] = coverage(metrics, errors)
    client = load_client()
    next_dates = upcoming(today)
    pipeline.stage("Escribiendo el análisis y el plan")
    from .ai.history import mock_history_plan, synthesize_history

    if mock_ai:
        plan = mock_history_plan(metrics, client.name)
    else:
        plan = synthesize_history(_for_ai(metrics), client, next_dates)
        plan["analysis_by"] = "Claude" if settings.ai_provider == "anthropic" else "GPT"
        pipeline.stage("Escribiendo las piezas con Gemini")
        plan = gemini_pieces(plan, metrics, client)
    pipeline.stage("Armando el informe")
    _copy_images(metrics, out)
    from .events import currency_symbol

    symbol = currency_symbol(metrics["currency"])
    html = _env().get_template("history.html.j2").render(
        client=client, brand=load_brand(), m=metrics, plan=plan, years=[str(y) for y in years], cur=symbol, next_dates=next_dates,
        generated_at=dt.datetime.now().strftime("%d/%m/%Y %H:%M"))
    (out / "report.html").write_text(html, encoding="utf-8")
    return {"report": str(out / "report.html"), "years": {y: yd["total"]["spend"] for y, yd in metrics["years"].items()},
            "events": len(metrics["events"]), "competition_ads": sum(len(c["ads"]) for c in metrics.get("competition") or []),
            "errors": errors}
