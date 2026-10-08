"""Análisis de una edición pasada de un evento comercial (CyberLunes, Black Friday...). Solo hechos.

Sobre los anuncios que arrancaron dentro de la ventana de la edición:
- cuándo arrancan respecto del día del evento (anticipo, previa, durante, después);
- qué ofertas usan y qué tan profundas (% de descuento, cuotas, envío gratis, regalos);
- cuánta urgencia comunican y con qué palabras;
- formatos, quién pautó y cuánto duraron.
"""
from __future__ import annotations

import datetime as dt
import re
import statistics
from collections import Counter
from typing import Any

PCT_RE = re.compile(r"(\d{1,2})\s?%")
CUOTAS_RE = re.compile(r"(\d{1,2})\s*cuotas", re.I)
OFFER_PATTERNS = {
    "descuento": re.compile(r"\d{1,2}\s?%\s*(off|de descuento|dto)|descuento|\boff\b", re.I),
    "cuotas": re.compile(r"cuotas|sin inter[eé]s|sin recargo", re.I),
    "envio_gratis": re.compile(r"env[ií]o gratis|env[ií]os gratis|sin costo de env[ií]o", re.I),
    "regalo": re.compile(r"regalo|de regalo|gratis con|2x1|3x2|llev[aá]", re.I),
    "precio_especial": re.compile(r"precio especial|precios? cyber|precio [uú]nico|mejor precio", re.I),
}
URGENCY_PATTERNS = {
    "solo hoy / por tiempo limitado": re.compile(r"solo hoy|s[oó]lo por hoy|por tiempo limitado|[uú]ltim[oa]s? (d[ií]a|horas)|termina|finaliza", re.I),
    "hasta agotar stock": re.compile(r"hasta agotar|stock limitado|quedan pocos|[uú]ltimas unidades", re.I),
    "cuenta regresiva / horas": re.compile(r"\b\d{1,2}\s?(hs|horas)\b|cuenta regresiva|faltan \d+", re.I),
    "fecha del evento": re.compile(r"cyber ?lunes|ciberlunes|cyber ?monday|black ?friday|black week", re.I),
}


def _text(it: dict[str, Any]) -> str:
    cls = it.get("cls") or {}
    return " ".join(str(x) for x in (it.get("headline"), it.get("body_text"), cls.get("offer"), " ".join(cls.get("key_claims") or [])) if x)


def phase(offset: int, length: int) -> str:
    if offset < -7:
        return "anticipo"
    if offset < 0:
        return "previa"
    if offset < max(1, length):
        return "durante"
    return "despues"


def peak_day(items: list[dict[str, Any]]) -> dt.date | None:
    """Sin fecha oficial: el día en que arrancaron más anuncios de la edición."""
    days = Counter(it["first_seen"] for it in items if it.get("first_seen"))
    if not days:
        return None
    return dt.date.fromisoformat(max(days.items(), key=lambda kv: (kv[1], kv[0]))[0])


def _dist(counter: Counter, total: int) -> list[dict[str, Any]]:
    return [{"value": k, "count": v, "pct": round(100 * v / total, 1) if total else 0.0} for k, v in counter.most_common()]


def analyze_event(items: list[dict[str, Any]], start: dt.date | None, end: dt.date | None, own_name: str = "") -> dict[str, Any]:
    items = [it for it in items if it.get("first_seen")]
    official = start is not None
    start = start or peak_day(items)
    end = end or start
    n = len(items)
    out: dict[str, Any] = {"ads": n, "event_start": start.isoformat() if start else None, "event_end": end.isoformat() if end else None,
                           "official_dates": official}
    if not n or start is None:
        return out
    length = (end - start).days + 1
    offsets, phases = [], Counter()
    for it in items:
        off = (dt.date.fromisoformat(it["first_seen"]) - start).days
        it["offset"] = off
        it["phase"] = phase(off, length)
        offsets.append(off)
        phases[it["phase"]] += 1
    durations = [it["days_active"] for it in items if it.get("status") == "inactive" and it.get("days_active")]
    offers, urg, pcts, cuotas = Counter(), Counter(), [], []
    for it in items:
        t = _text(it)
        for k, rx in OFFER_PATTERNS.items():
            if rx.search(t):
                offers[k] += 1
        for k, rx in URGENCY_PATTERNS.items():
            if rx.search(t):
                urg[k] += 1
        found = [int(x) for x in PCT_RE.findall(t) if 5 <= int(x) <= 90]
        if found:
            pcts.append(max(found))
        q = [int(x) for x in CUOTAS_RE.findall(t)]
        if q:
            cuotas.append(max(q))
    depth = Counter()
    for p in pcts:
        depth["hasta 20%" if p <= 20 else "21-40%" if p <= 40 else "41-60%" if p <= 60 else "más de 60%"] += 1
    advertisers = Counter(it["competitor"] for it in items)
    out.update({
        "timing": {
            "phases": {k: phases.get(k, 0) for k in ("anticipo", "previa", "durante", "despues")},
            "earliest_days_before": max(0, -min(offsets)),
            "median_offset": statistics.median(offsets),
            "by_day": sorted(Counter(offsets).items()),
        },
        "offers": {"dist": _dist(offers, n), "with_pct": len(pcts), "pct_max": max(pcts) if pcts else None,
                   "pct_median": statistics.median(pcts) if pcts else None, "depth": _dist(depth, len(pcts)),
                   "cuotas_max": max(cuotas) if cuotas else None},
        "urgency": _dist(urg, n),
        "formats": _dist(Counter(it.get("format") or "desconocido" for it in items), n),
        "duration_median": statistics.median(durations) if durations else None,
        "advertisers": [{"name": k, "ads": v, "own": k == own_name} for k, v in advertisers.most_common(15)],
        "own_ads": advertisers.get(own_name, 0) if own_name else 0,
    })
    return out


TYPE_ORDER = ["compras", "conversaciones", "leads", "clics al sitio"]


def _types(d: dict[str, Any]) -> dict[str, float]:
    if "by_type" in d:
        return d["by_type"] or {}
    return {d["result_type"]: d.get("results", 0)} if d.get("result_type") else {}  # datos viejos


def own_daily_summary(daily: list[dict[str, Any]], start: dt.date | None, end: dt.date | None) -> dict[str, Any]:
    """Inversión y cada tipo de resultado del cliente (compras, conversaciones, leads, clics) antes, durante y después.

    Los tipos nunca se suman entre sí: cada uno tiene su promedio por día y su costo.
    """
    if not daily:
        return {}
    phases: dict[str, dict[str, Any]] = {}
    for d in daily:
        day = dt.date.fromisoformat(d["date"])
        key = "sin_fechas" if not start else "antes" if day < start else "durante" if day <= (end or start) else "despues"
        p = phases.setdefault(key, {"days": 0, "spend": 0.0, "types": {}})
        p["days"] += 1
        p["spend"] = round(p["spend"] + d["spend"], 2)
        for k, v in _types(d).items():
            p["types"][k] = p["types"].get(k, 0) + v
    present = [t for t in TYPE_ORDER if any(p["types"].get(t) for p in phases.values())]
    for p in phases.values():
        p["spend_per_day"] = round(p["spend"] / p["days"], 2) if p["days"] else 0
        p["per_type"] = {t: {"total": round(p["types"].get(t, 0)), "per_day": round(p["types"].get(t, 0) / p["days"], 1) if p["days"] else 0,
                             "cost": round(p["spend"] / p["types"][t], 2) if p["types"].get(t) else None} for t in present}
    return {"types": present, "phases": phases, "days": daily,
            "note": "El costo de cada tipo divide toda la inversión del día por ese tipo de resultado (la cuenta tiene campañas con distintos objetivos)."}


def own_results(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Los anuncios propios de la edición con sus resultados reales, agrupados por tipo de resultado
    (compras, conversaciones, leads, clics) y, dentro de cada grupo, del mejor costo al peor."""
    rows = []
    for it in items:
        perf = (it.get("engagement") or {}).get("performance")
        if it.get("tier") != "own" or not perf:
            continue
        cls = it.get("cls") or {}
        head = it.get("headline") or ""
        if "{{" in head:
            head = "Anuncio de catálogo"
        rows.append({"id": it["id"], "headline": head, "format": it.get("format"), "thumbnail_rel": it.get("thumbnail_rel"),
                     "product": cls.get("product_shown") or cls.get("product") or "", "offer": cls.get("offer"),
                     **{k: perf.get(k) for k in ("spend", "results", "result_type", "cost_per_result", "ctr", "roas")}})
    rank = {t: i for i, t in enumerate(TYPE_ORDER)}
    return sorted(rows, key=lambda r: (rank.get(r["result_type"] or "", 9), r["cost_per_result"] is None, r["cost_per_result"] or 0))


def own_results_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Los anuncios propios por tipo de resultado, con la barra relativa al costo más alto del grupo."""
    groups: list[dict[str, Any]] = []
    for t in TYPE_ORDER + [""]:
        g = [dict(r) for r in rows if (r["result_type"] or "") == t]
        if not g:
            continue
        top = max((r["cost_per_result"] or 0) for r in g) or 1
        for r in g:
            r["bar"] = round(100 * (r["cost_per_result"] or 0) / top)
        groups.append({"type": t or "sin resultado medido", "rows": g})
    return groups
