"""Motor comparativo. Funciones puras sobre 'items' (dicts) para que sean testeables sin DB.

Un item = creativo + su clasificación:
{
  "id", "competitor", "competitor_slug", "tier", "platform", "format", "placements",
  "days_active", "status", "first_seen", "last_seen", "engagement", "thumbnail_path",
  "snapshot_url", "headline", "body_text", "cls": {campos de la taxonomía}
}
Todo lo que sale de acá es DATO OBSERVABLE (conteos, porcentajes, días). Las
interpretaciones viven en la síntesis (Etapa 2) y se marcan como inferencias.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any, Callable, Iterable

from ..config import Taxonomy


def creative_to_item(creative: Any, taxonomy: Taxonomy) -> dict[str, Any]:
    cls = creative.latest_classification
    data = dict(cls.data) if cls else {}
    if data.get("primary_angle") and not data.get("angle_family"):
        data["angle_family"] = taxonomy.family_of(data["primary_angle"])
    return {
        "id": creative.id,
        "competitor": creative.competitor.name,
        "competitor_slug": creative.competitor.slug,
        "tier": creative.competitor.tier,
        "country": creative.competitor.country,
        "platform": creative.platform,
        "source": creative.source,
        "format": (data.get("format_detected") or creative.format or "desconocido"),
        "placements": list(creative.placements or []),
        "days_active": creative.days_active,
        "status": creative.status,
        "first_seen": creative.first_seen.isoformat(),
        "last_seen": creative.last_seen.isoformat(),
        "engagement": dict(creative.engagement or {}),
        "thumbnail_path": creative.thumbnail_path,
        "snapshot_url": creative.snapshot_url,
        "landing_url": creative.landing_url,
        "headline": creative.headline,
        "body_text": creative.body_text,
        "qa_status": creative.qa_status,
        "confidence": cls.confidence if cls else "low",
        "cls": data,
    }


def distribution(items: Iterable[dict[str, Any]], getter: Callable[[dict[str, Any]], Any]) -> list[dict[str, Any]]:
    counts: Counter = Counter()
    for it in items:
        v = getter(it)
        if v:
            counts[v] += 1
    total = sum(counts.values()) or 1
    return [
        {"value": k, "count": n, "pct": round(100 * n / total, 1)}
        for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def weighted_distribution(items: Iterable[dict[str, Any]], getter: Callable[[dict[str, Any]], Any]) -> list[dict[str, Any]]:
    """Distribución donde cada competidor pesa lo mismo (promedio de sus distribuciones).

    Evita que un anunciante con 100 creativos domine el mapa frente a uno con 3.
    `count` = cantidad de competidores que usan ese valor.
    """
    per_comp: dict[str, Counter] = defaultdict(Counter)
    for it in items:
        v = getter(it)
        if v:
            per_comp[it["competitor"]][v] += 1
    if not per_comp:
        return []
    acc: dict[Any, float] = defaultdict(float)
    users: Counter = Counter()
    for comp, counts in per_comp.items():
        total = sum(counts.values())
        for v, n in counts.items():
            acc[v] += 100 * n / total
            users[v] += 1
    n_comp = len(per_comp)
    return [
        {"value": v, "count": users[v], "pct": round(acc[v] / n_comp, 1)}
        for v in sorted(acc, key=lambda x: (-acc[x], str(x)))
    ]


def _cls(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda it: (it.get("cls") or {}).get(key)


def angle_matrix(items: Iterable[dict[str, Any]], families: list[str]) -> dict[str, dict[str, int]]:
    """Competidor -> familia de ángulo -> cantidad de creativos. Mapa de comunicación, no ranking."""
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: {f: 0 for f in families})
    for it in items:
        fam = (it.get("cls") or {}).get("angle_family")
        if fam in families:
            matrix[it["competitor"]][fam] += 1
        else:
            matrix[it["competitor"]]  # asegura fila
    return {k: dict(v) for k, v in sorted(matrix.items())}


def longevity(items: list[dict[str, Any]]) -> dict[str, Any]:
    days = [it["days_active"] for it in items]
    buckets = {"0-6": 0, "7-30": 0, "31-90": 0, "91+": 0}
    for d in days:
        if d < 7:
            buckets["0-6"] += 1
        elif d <= 30:
            buckets["7-30"] += 1
        elif d <= 90:
            buckets["31-90"] += 1
        else:
            buckets["91+"] += 1
    top = sorted(items, key=lambda it: -it["days_active"])[:10]
    return {
        "buckets": buckets,
        "median_days": statistics.median(days) if days else 0,
        "mean_days": round(statistics.mean(days), 1) if days else 0,
        "top_ids": [it["id"] for it in top],
    }


def white_space(dist: list[dict[str, Any]], universe: list[str], threshold_pct: float) -> list[dict[str, Any]]:
    """Valores del universo con participación menor al umbral (incluye los que no aparecen)."""
    present = {d["value"]: d["pct"] for d in dist}
    return sorted(
        ({"value": v, "pct": present.get(v, 0.0)} for v in universe if present.get(v, 0.0) < threshold_pct),
        key=lambda d: d["pct"],
    )


def month_over_month(current: list[dict[str, Any]], previous: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prev = {d["value"]: d["pct"] for d in previous}
    cur = {d["value"]: d["pct"] for d in current}
    rows = []
    for v in sorted(set(prev) | set(cur), key=lambda x: -(cur.get(x, 0.0))):
        rows.append(
            {
                "value": v,
                "pct": cur.get(v, 0.0),
                "prev_pct": prev.get(v, 0.0),
                "delta": round(cur.get(v, 0.0) - prev.get(v, 0.0), 1),
            }
        )
    return rows


def ours_vs_market(market: list[dict[str, Any]], ours: list[dict[str, Any]], key: str) -> dict[str, Any]:
    m = distribution(market, _cls(key))
    o = distribution(ours, _cls(key))
    mp = {d["value"]: d["pct"] for d in m}
    op = {d["value"]: d["pct"] for d in o}
    gaps = []
    for v in sorted(set(mp) | set(op), key=lambda x: -abs(mp.get(x, 0) - op.get(x, 0))):
        gaps.append({"value": v, "market_pct": mp.get(v, 0.0), "ours_pct": op.get(v, 0.0), "gap": round(op.get(v, 0.0) - mp.get(v, 0.0), 1)})
    return {"market": m, "ours": o, "gaps": gaps}


def build_metrics(
    items: list[dict[str, Any]],
    taxonomy: Taxonomy,
    previous_items: list[dict[str, Any]] | None = None,
    own_items: list[dict[str, Any]] | None = None,
    period: str = "",
) -> dict[str, Any]:
    """Todas las métricas del mes en un solo dict serializable (se guarda en Run.metrics)."""
    market = [it for it in items if it.get("tier") != "own"]
    classified = [it for it in market if it.get("cls")]
    previous_items = [it for it in (previous_items or []) if it.get("tier") != "own" and it.get("cls")]
    own_items = own_items if own_items is not None else [it for it in items if it.get("tier") == "own"]

    fam_dist = distribution(classified, _cls("angle_family"))
    hook_dist = distribution(classified, _cls("hook_type"))
    by_tier: dict[str, Any] = {}
    for tier in ("local", "global"):
        sub = [it for it in classified if it["tier"] == tier]
        by_tier[tier] = {
            "creatives": len(sub),
            "angle_family_dist": distribution(sub, _cls("angle_family")),
            "hook_dist": distribution(sub, _cls("hook_type")),
            "format_dist": distribution(sub, lambda it: it["format"]),
            "funnel_dist": distribution(sub, _cls("funnel_stage")),
            "style_dist": distribution(sub, _cls("creative_style")),
        }
    by_platform = {
        p: {
            "creatives": len([it for it in market if it["platform"] == p]),
            "format_dist": distribution([it for it in classified if it["platform"] == p], lambda it: it["format"]),
            "angle_family_dist": distribution([it for it in classified if it["platform"] == p], _cls("angle_family")),
        }
        for p in sorted({it["platform"] for it in market})
    }
    return {
        "period": period,
        "totals": {
            "creatives": len(market),
            "active": len([it for it in market if it["status"] == "active"]),
            "classified": len(classified),
            "competitors": len({it["competitor_slug"] for it in market}),
            "own_creatives": len(own_items),
            "platforms": dict(Counter(it["platform"] for it in market)),
            "by_competitor": dict(sorted(Counter(it["competitor"] for it in market).items())),
        },
        "angle_family_dist": fam_dist,
        "angle_family_dist_weighted": weighted_distribution(classified, _cls("angle_family")),
        "angle_dist": distribution(classified, _cls("primary_angle")),
        "hook_dist": hook_dist,
        "hook_dist_weighted": weighted_distribution(classified, _cls("hook_type")),
        "format_dist_weighted": weighted_distribution(classified, lambda it: it["format"]),
        "format_dist": distribution(classified, lambda it: it["format"]),
        "placement_dist": distribution(market, lambda it: ",".join(it["placements"]) if it["placements"] else None),
        "funnel_dist": distribution(classified, _cls("funnel_stage")),
        "product_dist": distribution(classified, _cls("product")),
        "style_dist": distribution(classified, _cls("creative_style")),
        "cta_dist": distribution(classified, _cls("cta_type")),
        "pain_point_dist": distribution(classified, _cls("pain_point")),
        "by_tier": by_tier,
        "by_platform": by_platform,
        "angle_matrix": angle_matrix(classified, taxonomy.families),
        "longevity": longevity(market),
        "white_space": {
            "threshold_pct": taxonomy.white_space_threshold_pct,
            "angle_families": white_space(fam_dist, taxonomy.families, taxonomy.white_space_threshold_pct),
            "hooks": white_space(hook_dist, taxonomy.hook_types, taxonomy.white_space_threshold_pct),
        },
        "mom": {
            "has_previous": bool(previous_items),
            "previous_creatives": len(previous_items),
            "angle_family": month_over_month(fam_dist, distribution(previous_items, _cls("angle_family"))),
            "hook": month_over_month(hook_dist, distribution(previous_items, _cls("hook_type"))),
            "product": month_over_month(distribution(classified, _cls("product")), distribution(previous_items, _cls("product"))),
        },
        "ours_vs_market": {
            "has_own": bool(own_items),
            "angle_family": ours_vs_market(classified, [o for o in own_items if o.get("cls")], "angle_family"),
            "hook": ours_vs_market(classified, [o for o in own_items if o.get("cls")], "hook_type"),
            "product": ours_vs_market(classified, [o for o in own_items if o.get("cls")], "product"),
        },
    }
