"""Qué le funciona al cliente: sus propios anuncios con resultados reales (Marketing API), por mensaje y formato."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def _perf(it: dict[str, Any]) -> dict[str, Any] | None:
    return (it.get("engagement") or {}).get("performance")


def _agg(rows: list[dict[str, Any]]) -> dict[str, Any]:
    spend = sum(r["spend"] for r in rows)
    impr = sum(r["impressions"] for r in rows)
    clicks = sum(r["clicks"] for r in rows)
    results = sum(r["results"] for r in rows)
    return {"ads": len(rows), "spend": round(spend, 2), "ctr": round(100 * clicks / impr, 2) if impr else None,
            "results": results, "cost_per_result": round(spend / results, 2) if results else None}


def own_performance(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    own = [it for it in items if it.get("tier") == "own" and _perf(it)]
    if not own:
        return None
    perfs = [_perf(it) for it in own]
    result_type = max({p["result_type"] for p in perfs if p["result_type"]} or {""}, key=lambda t: sum(1 for p in perfs if p["result_type"] == t))
    out: dict[str, Any] = {"result_type": result_type, "period": perfs[0].get("period"), "total": _agg(perfs)}
    for key, getter in (("by_family", lambda it: (it.get("cls") or {}).get("angle_family")),
                        ("by_hook", lambda it: (it.get("cls") or {}).get("hook_type")),
                        ("by_format", lambda it: it.get("format"))):
        groups: dict[Any, list] = defaultdict(list)
        for it in own:
            groups[getter(it) or "sin_dato"].append(_perf(it))
        out[key] = sorted(({"value": k, **_agg(v)} for k, v in groups.items()), key=lambda r: -r["spend"])
    ranked = sorted((it for it in own if _perf(it)["results"]), key=lambda it: _perf(it)["cost_per_result"])
    out["best"] = [{"id": it["id"], "headline": (it.get("headline") or "")[:120], **_perf(it),
                    "angle_family": (it.get("cls") or {}).get("angle_family"), "format": it.get("format")} for it in ranked[:3]]
    out["worst"] = [{"id": it["id"], "headline": (it.get("headline") or "")[:120], **_perf(it),
                     "angle_family": (it.get("cls") or {}).get("angle_family"), "format": it.get("format")} for it in ranked[-3:][::-1]] if len(ranked) > 3 else []
    return out
