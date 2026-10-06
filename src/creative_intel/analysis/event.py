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
