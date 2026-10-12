"""Con qué datos se armó cada informe y qué faltó, con un nivel de confianza calculado (no lo decide la IA).

Va arriba del informe y también a la IA: con poca cobertura, las ideas se plantean como pruebas chicas.
"""
from __future__ import annotations

from typing import Any

LEVELS = [(5, "alta"), (3, "media"), (0, "baja")]


def _level(points: int) -> str:
    return next(name for floor, name in LEVELS if points >= floor)


def _row(source: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"source": source, "ok": ok, "detail": detail}


def event_coverage(m: dict[str, Any], live: bool = False) -> dict[str, Any]:
    errors = " ".join(m.get("collect_errors") or []).lower()
    cur = m.get("currency") or "US$"
    rows, pts = [], 0
    own = m.get("own_results") or []
    spend = sum(r.get("spend") or 0 for r in own)
    if own:
        pts += 2
        rows.append(_row("Anuncios propios con resultados (Meta)", True, f"{len(own)} anuncios, {cur} " + f"{spend:,.0f}".replace(",", ".") + " invertidos"))
    elif "anuncios propios" in errors:
        rows.append(_row("Anuncios propios con resultados (Meta)", False, "no se pudieron leer (ver el error abajo)"))
    else:
        rows.append(_row("Anuncios propios con resultados (Meta)", False, "sin pauta en esas fechas"))
    phases = (m.get("own_daily") or {}).get("phases") or {}
    rows.append(_row("Resultados día por día", bool(phases), "antes, durante y después del evento" if phases else "no hay"))
    organic = m.get("organic") or []
    own_ig = next((o for o in organic if o.get("own")), None)
    if own_ig and own_ig.get("posts"):
        pts += 1
    rows.append(_row("Instagram propio", bool(own_ig and own_ig.get("posts")),
                     f"{own_ig['posts']} publicaciones" if own_ig and own_ig.get("posts") else "sin publicaciones leídas"))
    n_ads = m.get("ads") or 0
    pts += 2 if n_ads >= 10 else 1 if n_ads >= 3 else 0
    rows.append(_row("Anuncios de la competencia (Meta y Google)", n_ads >= 3,
                     f"{n_ads} anuncios" + ("" if live or n_ads >= 10 else " · Meta no guarda los anuncios terminados fuera de la UE; de ediciones pasadas queda lo de Google")))
    comp = [o for o in organic if not o.get("own")]
    with_posts = [o for o in comp if o.get("posts")]
    pts += 2 if len(with_posts) >= 2 else 1 if with_posts else 0
    partial = [o for o in comp if o.get("source") == "perfil_publico"]
    capped = [o for o in comp if o.get("capped") and not o.get("posts")]
    if capped:
        detail_cap = f" · de {len(capped)} no se llegó a esas fechas (subí APIFY_MAX_POSTS)"
    else:
        detail_cap = ""
    detail = f"{len(with_posts)} de {len(comp)} cuentas con publicaciones en esas fechas" if comp else "no se pudo leer ninguna cuenta"
    if partial:
        detail += f" · de {len(partial)} solo se ven las últimas publicaciones"
    rows.append(_row("Instagram de la competencia", bool(with_posts), detail + detail_cap))
    if any(o.get("event") is not None for o in organic):
        about = [o for o in organic if (o.get("event") or {}).get("posts")]
        rows.append(_row("Publicaciones del evento en sí (Instagram)", bool(about),
                         f"{len(about)} de {len(organic)} cuentas publicaron sobre la fecha o en sus días"))
    if "own_event_results" in m:
        ev = m.get("own_event_results") or []
        rows.append(_row("Pauta propia en los días del evento", bool(ev), f"{len(ev)} anuncios con inversión esos días" if ev else "ninguno"))
    level = _level(pts)
    return {"level": level, "rows": rows, "errors": m.get("collect_errors") or [],
            "missing": [r["source"] for r in rows if not r["ok"]]}


def plan_coverage(m: dict[str, Any], n_reference: int, n_research: int) -> dict[str, Any]:
    rows, pts = [], 0
    audit = m.get("own_audit") or {}
    active = audit.get("active") or 0
    pts += 1 if active else 0
    rows.append(_row("Anuncios propios al aire (Biblioteca de Meta)", bool(active), f"{active} anuncios" if active else "ninguno"))
    perf = m.get("own_performance") or {}
    total = (perf.get("total") or {})
    pts += 2 if perf else 0
    rows.append(_row("Resultados propios (cuenta publicitaria)", bool(perf), f"{total.get('ads', 0)} anuncios con resultados" if perf else "no hay"))
    ig = m.get("own_instagram") or {}
    pts += 1 if ig.get("posts") else 0
    rows.append(_row("Instagram propio", bool(ig.get("posts")), f"{ig.get('posts')} publicaciones" if ig.get("posts") else "sin publicaciones leídas"))
    pts += 2 if n_reference >= 30 else 1 if n_reference >= 10 else 0
    rows.append(_row("Anuncios de referencia (competencia y mercado)", n_reference >= 10, f"{n_reference} anuncios"))
    rows.append(_row("Investigación web", n_research > 0, f"{n_research} hallazgos" if n_research else "no se hizo"))
    return {"level": _level(pts), "rows": rows, "errors": [], "missing": [r["source"] for r in rows if not r["ok"]]}
