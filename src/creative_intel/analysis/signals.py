"""Señales observables de que a un anunciante le sirve un anuncio. Solo hechos, sin interpretar.

- Días al aire (cuando la plataforma informa el inicio).
- Versiones: cuántas variantes de la misma pieza circulan (Google "N variaciones", Meta "N anuncios usan este contenido").
- Meses seguidos entre los más vistos: cuántas corridas mensuales lo encontraron.
- Movimiento del mes por competidor: anuncios que entraron al top, que siguen y que salieron.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

SHARED_RE = re.compile(r"(\d+)\s+(?:anuncios? usan? este contenido|ads? uses? this creative)", re.I)


def versions_of(platform: str, raw: dict[str, Any] | None) -> int:
    raw = raw or {}
    if platform == "google":
        try:
            return max(1, int(raw.get("variations") or 1))
        except (TypeError, ValueError):
            return 1
    m = SHARED_RE.search(raw.get("card_text") or "")
    if m:
        return max(1, int(m.group(1)))
    return 2 if raw.get("multiple_versions") else 1


def signal_list(it: dict[str, Any], start_known: bool) -> list[str]:
    out = []
    if start_known and it.get("days_active"):
        out.append(f"{it['days_active']} días al aire")
    if (it.get("versions") or 1) > 1:
        out.append(f"{it['versions']} versiones")
    if (it.get("months_in_top") or 1) > 1:
        out.append(f"{it['months_in_top']} meses seguidos entre los más vistos")
    if it.get("track") == "nuevo":
        out.append("entró este mes")
    return out


def signal_rank(it: dict[str, Any], start_known: bool) -> tuple:
    """Más meses en el top, más días al aire (si se conocen) y más versiones primero."""
    return (-(it.get("months_in_top") or 1), -(it["days_active"] if start_known else 0), -(it.get("versions") or 1))


def movement(current: list[dict[str, Any]], previous: list[dict[str, Any]]) -> dict[str, Any]:
    """Por competidor y plataforma: qué anuncios entraron al top, cuáles siguen y cuáles salieron (vs. el mes anterior).

    Solo se compara donde hubo datos los dos meses; si no, ese competidor queda como 'primera medición'.
    """
    prev_by: dict[tuple, set] = defaultdict(set)
    for it in previous:
        prev_by[(it["competitor"], it["platform"])].add(it["id"])
    cur_by: dict[tuple, set] = defaultdict(set)
    for it in current:
        cur_by[(it["competitor"], it["platform"])].add(it["id"])
    rows: dict[str, dict[str, int]] = {}
    for key in sorted(set(cur_by) | set(prev_by)):
        name, _platform = key
        row = rows.setdefault(name, {"new": 0, "kept": 0, "dropped": 0, "first_time": 0})
        cur, prev = cur_by.get(key, set()), prev_by.get(key, set())
        if not prev:
            row["first_time"] += len(cur)
            continue
        row["new"] += len(cur - prev)
        row["kept"] += len(cur & prev)
        row["dropped"] += len(prev - cur)
    track = {}
    for it in current:
        prev = prev_by.get((it["competitor"], it["platform"]))
        it["track"] = track[str(it["id"])] = ("sigue" if it["id"] in prev else "nuevo") if prev else "primera"
    return {"has_previous": bool(previous), "by_competitor": rows, "track": track}


def build_signals(market: list[dict[str, Any]], previous: list[dict[str, Any]], concepts: list[dict[str, Any]],
                  start_known_fn) -> dict[str, Any]:
    mov = movement(market, previous)
    for it in market:
        it["signals"] = signal_list(it, start_known_fn(it))
    ranked = sorted(market, key=lambda it: signal_rank(it, start_known_fn(it)))
    top = [
        {"id": it["id"], "competitor": it["competitor"], "platform": it["platform"], "headline": (it.get("headline") or "")[:140],
         "signals": it["signals"], "angle": (it.get("cls") or {}).get("primary_angle"), "format": it.get("format")}
        for it in ranked if it["signals"]
    ][:12]
    repeated = [
        {"competitor": c["competitor"], "name": c["name"], "variants": c["variants"], "first_seen": c["first_seen"], "angle": c["angle"],
         "product": c["product"], "hook_type": c["hook_type"]}
        for c in concepts if c.get("variants", 1) >= 2
    ][:12]
    return {"movement": mov, "top": top, "repeated_ideas": repeated}
