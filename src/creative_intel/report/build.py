"""Genera el reporte mensual en HTML (visual), Markdown y una versión HTML simplificada para Basecamp."""
from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).parent / "templates"

MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def period_label(period: str) -> str:
    y, m = period.split("-")
    return f"{MONTHS_ES[int(m) - 1].capitalize()} {y}"


def next_period_label(period: str) -> str:
    y, m = (int(x) for x in period.split("-"))
    m += 1
    if m > 12:
        m, y = 1, y + 1
    return f"{MONTHS_ES[m - 1].capitalize()} {y}"


def dots(n: int, max_n: int, width: int = 5) -> str:
    if max_n <= 0 or n <= 0:
        return "·"
    filled = max(1, round(width * n / max_n))
    return "●" * filled + "○" * (width - filled)


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    env.filters["dots"] = dots
    env.filters["bar"] = lambda pct: max(2, min(100, int(round(float(pct or 0)))))
    env.filters["of10"] = lambda pct: max(1, round(float(pct or 0) / 10)) if pct else 0
    env.filters["dias"] = lambda d: "—" if d is None else f"{int(round(float(d)))} días"
    env.filters["n"] = lambda v: f"{int(v):,}".replace(",", ".") if isinstance(v, (int, float)) else v
    return env


PRIORITY_ORDER = {"alta": 0, "high": 0, "media": 1, "medium": 1, "baja": 2, "low": 2}


def compare_rows(a: list[dict[str, Any]], b: list[dict[str, Any]] | None, top: int = 6) -> list[dict[str, Any]]:
    """Une dos distribuciones (competidor vs referencia) para barras pareadas."""
    pa = {d["value"]: d["pct"] for d in a or []}
    pb = {d["value"]: d["pct"] for d in b or []}
    keys = sorted(set(pa) | set(pb), key=lambda k: (-pa.get(k, 0), -pb.get(k, 0)))[:top]
    return [{"value": k, "a": pa.get(k, 0.0), "b": pb.get(k, 0.0)} for k in keys]


DIMENSIONS = [
    ("angle_family_dist", "Qué mensaje usa", "La idea principal con la que vende."),
    ("hook_dist", "Cómo engancha", "El recurso de los primeros segundos o la primera línea."),
    ("format_dist", "En qué formato", "Video, imagen, carrusel o texto de búsqueda."),
    ("funnel_dist", "En qué etapa del cliente", "Descubrimiento, consideración o conversión."),
    ("product_dist", "Qué producto empuja", ""),
]


def _norm_summary(points: list[Any]) -> list[dict[str, str]]:
    out = []
    for p in points or []:
        out.append({"headline": p, "detail": "", "action": ""} if isinstance(p, str) else p)
    return out


def _has_preview(it: dict[str, Any]) -> bool:
    raw = it.get("raw") if isinstance(it.get("raw"), dict) else {}
    for path in (it.get("thumbnail_path"), (raw or {}).get("screenshot")):
        if path and Path(path).is_file():
            return True
    return False


def top_creatives(items: list[dict[str, Any]], n: int = 10, per_competitor: int = 3) -> list[dict[str, Any]]:
    """Top por señales observables (meses seguidos en el top, días al aire, versiones), con tope por competidor."""
    from ..analysis.metrics import start_known
    from ..analysis.signals import signal_list, signal_rank

    for it in items:
        it.setdefault("signals", signal_list(it, start_known(it)))
    ranked = sorted(items, key=lambda it: (not _has_preview(it), *signal_rank(it, start_known(it)), it["id"]))
    out, per = [], {}
    for it in ranked:
        if per.get(it["competitor"], 0) >= per_competitor:
            continue
        out.append(it)
        per[it["competitor"]] = per.get(it["competitor"], 0) + 1
        if len(out) >= n:
            break
    return out


def build_report_context(
    period: str,
    metrics: dict[str, Any],
    synthesis: dict[str, Any],
    items: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    review: dict[str, Any] | None = None,
    client: Any = None,
    brand: Any = None,
    taxonomy: Any = None,
) -> dict[str, Any]:
    from ..config import load_brand, load_client, load_taxonomy

    client = client or load_client()
    brand = brand or load_brand()
    taxonomy = taxonomy or load_taxonomy()
    market_items = [it for it in items if it.get("tier") != "own"]
    track = ((metrics.get("signals") or {}).get("movement") or {}).get("track") or {}
    for it in market_items:
        it["track"] = track.get(str(it["id"]), "primera")
    by_id = {it["id"]: it for it in items}
    syn_refs = {r.get("creative_id"): r for r in synthesis.get("reference_creatives", []) or []}

    # Referencia de comparación: el cliente si tiene anuncios cargados; si no, el promedio del mercado
    own = metrics.get("own_profile")
    weighted = metrics.get("market_profile_weighted") or {}
    baseline_name = client.name if own else "Promedio del mercado"
    baseline = own or weighted

    tabs = []
    for prof in metrics.get("competitor_profiles") or []:
        comp_items = [it for it in market_items if it["competitor"] == prof["name"]]
        tabs.append({
            "id": "c" + str(len(tabs)),
            "name": prof["name"],
            "tier": prof["tier"],
            "profile": prof,
            "compare": [
                {"key": k, "title": t, "help": h, "rows": compare_rows(prof.get(k) or [], (baseline or {}).get(k) or [])}
                for k, t, h in DIMENSIONS
            ],
            "top": top_creatives(comp_items, n=4, per_competitor=4),
        })

    local_top = top_creatives([it for it in market_items if it["tier"] == "local"])
    global_top = top_creatives([it for it in market_items if it["tier"] == "global"])
    for it in local_top + global_top:
        it["ref"] = syn_refs.get(it["id"])

    matrix = metrics.get("angle_matrix", {})
    heat = {}
    for comp, row in matrix.items():
        tot = sum(row.values()) or 1
        heat[comp] = {f: round(100 * n / tot) for f, n in row.items()}
    today = dt.date.today()
    actions = sorted(synthesis.get("action_items", []) or [], key=lambda a: (PRIORITY_ORDER.get(str(a.get("priority", "media")).lower(), 1), a.get("due_in_days", 0)))
    for a in actions:
        a["due_date"] = (today + dt.timedelta(days=int(a.get("due_in_days") or 0))).strftime("%d/%m")
    opportunities = sorted((synthesis.get("white_space") or []) + (synthesis.get("recommendations") or []), key=lambda o: PRIORITY_ORDER.get(str(o.get("priority", "media")).lower(), 1))
    risks = sorted(synthesis.get("risks") or [], key=lambda r: PRIORITY_ORDER.get(str(r.get("severity", "media")).lower(), 1))

    own_compare = [
        {"title": t, "rows": compare_rows(own.get(k) or [], weighted.get(k) or [])} for k, t, _ in DIMENSIONS if own
    ] if own else []
    display_ids = {it["id"] for it in local_top + global_top} | {it["id"] for t in tabs for it in t["top"]}
    return {
        "period": period,
        "period_label": period_label(period),
        "next_period_label": next_period_label(period),
        "generated_at": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
        "client": client,
        "brand": brand,
        "lbl": taxonomy.label,
        "metrics": metrics,
        "synthesis": synthesis,
        "summary_points": _norm_summary(synthesis.get("executive_summary")),
        "tabs": tabs,
        "baseline_name": baseline_name,
        "has_own": bool(own),
        "own_compare": own_compare,
        "local_top": local_top,
        "global_top": global_top,
        "opportunities": opportunities,
        "risks": risks,
        "actions": actions,
        "families": taxonomy.families,
        "heat": heat,
        "profiles": metrics.get("competitor_profiles") or [],
        "review": review or {},
        "display_ids": display_ids,
        # compatibilidad con plantillas md/basecamp
        "reference_creatives": [{**r, "item": by_id[r["creative_id"]]} for r in synthesis.get("reference_creatives", []) or [] if r.get("creative_id") in by_id],
        "concepts": concepts[:15],
        "items": sorted(items, key=lambda it: (it["tier"], it["competitor"], -it["days_active"])),
        "matrix": matrix,
    }


def build_plan_context(period: str, metrics: dict[str, Any], plan: dict[str, Any], items: list[dict[str, Any]],
                       review: dict[str, Any] | None = None) -> dict[str, Any]:
    """Contexto del plan editorial: cada id de anuncio citado se cambia por el anuncio (con su imagen)."""
    from ..config import load_brand, load_client, load_taxonomy

    client, brand, taxonomy = load_client(), load_brand(), load_taxonomy()
    by_id = {it["id"]: it for it in items}
    findings = {f["url"]: f for f in plan.get("research") or []}
    gallery = [{**g, "item": by_id[g["creative_id"]]} for g in plan.get("gallery") or [] if g.get("creative_id") in by_id]
    ideas = []
    for n, idea in enumerate(plan.get("ideas") or [], 1):
        ref = by_id.get(idea.get("reference_creative_id"))
        ideas.append({**idea, "num": f"{n:02d}", "ref_item": ref, "finding": findings.get(idea.get("reference_url") or "")})
    shown = [g["item"] for g in gallery] + [i["ref_item"] for i in ideas if i["ref_item"]]
    sources = []
    for it in shown:
        if it.get("snapshot_url") and all(s["url"] != it["snapshot_url"] for s in sources):
            quote = (it.get("cls") or {}).get("copy_quote") or it.get("headline") or ""
            sources.append({"url": it["snapshot_url"], "text": f"{it['competitor']} — “{quote[:90]}”" + (f", desde {it['first_seen']}" if it.get("first_seen") else "")})
    for f in plan.get("research") or []:
        sources.append({"url": f["url"], "text": f"{f['who']} — {f['fact'][:110]}"})
    audit = metrics.get("own_audit") or {}
    own_ig = dict(metrics.get("own_instagram") or {})
    return {
        "own_ig": own_ig,
        "plan": plan, "period": period, "period_label": period_label(period), "next_period_label": next_period_label(period),
        "generated_at": dt.datetime.now().strftime("%d/%m/%Y %H:%M"), "client": client, "brand": brand, "lbl": taxonomy.label,
        "metrics": metrics, "synthesis": plan, "review": review or {}, "audit": audit, "execution": metrics.get("execution") or {},
        "own_perf": metrics.get("own_performance") or {}, "coverage": metrics.get("coverage") or [],
        "gallery": gallery, "ideas": ideas, "sources": sources, "items": items,
        "n_refs": sum(1 for it in items if it.get("tier") != "own"),
        "display_ids": {it["id"] for it in shown},
    }


def render_assets(items: list[dict[str, Any]], out_dir: str | Path, wanted: set | None = None) -> None:
    """Copia la imagen de cada anuncio junto al informe y le deja a cada item su ruta relativa."""
    out = Path(out_dir)
    assets_out = out / "assets"
    assets_out.mkdir(parents=True, exist_ok=True)
    for it in items:
        if wanted is not None and it["id"] not in wanted:
            continue
        raw = it.get("raw") if isinstance(it.get("raw"), dict) else {}
        # Primero la captura de lo que se ve en el anuncio (imagen y texto coinciden); la imagen bajada, de respaldo
        for p in ((raw or {}).get("screenshot"), it.get("thumbnail_path")):
            if p and Path(p).is_file():
                dest = assets_out / Path(p).name
                if not dest.exists():
                    shutil.copy(p, dest)
                it["thumbnail_rel"] = f"assets/{Path(p).name}"
                break


def render_report(context: dict[str, Any], out_dir: str | Path, copy_assets: bool = True) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if copy_assets:
        render_assets(context["items"], out, context.get("display_ids"))
    env = _env()
    outputs: dict[str, Path] = {}
    templates = ((("plan.html.j2", "report.html"), ("plan.md.j2", "report.md"), ("plan_basecamp.html.j2", "basecamp.html"))
                 if context.get("plan") else
                 (("report.html.j2", "report.html"), ("report.md.j2", "report.md"), ("basecamp.html.j2", "basecamp.html")))
    for tpl, name in templates:
        rendered = env.get_template(tpl).render(**context)
        path = out / name
        path.write_text(rendered, encoding="utf-8")
        outputs[name] = path
    return outputs
