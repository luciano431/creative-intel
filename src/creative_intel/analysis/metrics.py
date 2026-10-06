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

import re
import statistics
from collections import Counter, defaultdict
from typing import Any, Callable, Iterable

from ..config import Taxonomy
from .signals import versions_of


def clean_headline(text: str | None) -> str:
    """Descarta titulares que en realidad son ruido de la interfaz de las bibliotecas."""
    from ..collectors.meta_web import TIMESTAMP_RE, UI_NOISE

    t = (text or "").strip()
    if not t or TIMESTAMP_RE.match(t) or UI_NOISE.search(t):
        return ""
    return t


# Señales de promoción en el texto del anuncio. "Cotizá gratis" no es una promo: por eso
# "gratis" solo cuenta pegado a un período ("1 mes gratis"), y "100% online" tampoco es una promo.
PROMO_RE = re.compile(
    r"(?<!\d)\d{1,2}\s?%(?!\s*(?:online|digital|seguro|garantizad))|%\s?off|\bdescuentos?\b|\bdto\b|bonificaci[oó]n|bonificad[oa]s?\b|"
    r"cuotas?\s+sin\s+(?:recargo|inter[eé]s)|\b\d+\s+(?:mes|meses|cuota|cuotas)\s+(?:gratis|bonificad\w*)|"
    r"\bmes(?:es)?\s+gratis|\b2\s?x\s?1\b|\b3\s?x\s?2\b|\bpromo(?:ci[oó]n|ciones)?\b|precio\s+especial|"
    r"cashback|reintegro|\bsorteo|\bregalo\b|beneficio\s+exclusivo",
    re.I,
)
_NO_OFFER = {"", "null", "none", "ninguna", "ninguno", "no", "n/a", "sin oferta", "no aplica"}


def is_heuristic(cls: dict[str, Any]) -> bool:
    """La clasificación salió de la regla simple (sin IA o por un error de la API)."""
    summary = str(cls.get("summary") or "")
    return "clasificación heurística" in summary or "[fallback por error" in summary


def promo_of(it: dict[str, Any]) -> str | None:
    """La promoción que comunica el anuncio, aunque su mensaje principal sea otro.

    Fuente 1: el campo `offer` de la clasificación (la IA lee también el texto dentro de la imagen).
    Fuente 2: el texto del anuncio, por si la clasificación es la heurística o lo pasó por alto.
    """
    cls = it.get("cls") or {}
    offer = str(cls.get("offer") or "").strip()
    # La oferta guardada por la regla simple no es confiable ("100% online"): se recalcula del texto.
    if offer.lower() not in _NO_OFFER and not is_heuristic(cls):
        return offer
    text = "\n".join(t for t in (it.get("headline"), it.get("body_text")) if t)
    m = PROMO_RE.search(text)
    if not m:
        return None
    # La oración que contiene la promo: corta en punto, signo de cierre o salto de línea.
    start = max(text.rfind(ch, 0, m.start()) for ch in ".!?\n") + 1
    ends = [i for i in (text.find(ch, m.end()) for ch in ".!?\n") if i != -1]
    return text[start: min(ends) if ends else len(text)].strip()[:120]


def promo_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    found = [(it, promo_of(it)) for it in items]
    with_promo = [(it, p) for it, p in found if p]
    examples: list[str] = []
    for _, p in with_promo:
        if p.lower() not in (e.lower() for e in examples):
            examples.append(p)
        if len(examples) == 3:
            break
    return {
        "ads": len(with_promo),
        "total": len(items),
        "pct": round(100 * len(with_promo) / len(items), 1) if items else 0.0,
        "examples": examples,
    }


def start_known(it: dict[str, Any]) -> bool:
    """Google no informa cuándo empezó un anuncio: sin esa fecha la duración no es medible."""
    raw = it.get("raw") or {}
    if it.get("platform") == "google":
        return bool(raw.get("first_seen_known"))
    return True


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
        "raw": {k: (creative.raw or {}).get(k) for k in ("screenshot", "variations", "text_in_image", "regions", "first_seen_known")},
        "versions": versions_of(creative.platform, creative.raw),
        "months_in_top": len({s.run_id for s in (creative.sightings or [])}) or 1,
        "snapshot_url": creative.snapshot_url,
        "landing_url": creative.landing_url,
        "headline": clean_headline(creative.headline),
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
    """Duración al aire. Solo cuenta anuncios con fecha de inicio conocida (no los de Google)."""
    items = [it for it in items if start_known(it)]
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
        "median_days": statistics.median(days) if days else None,
        "mean_days": round(statistics.mean(days), 1) if days else None,
        "dated": len(days),
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


def profile(items: list[dict[str, Any]], name: str = "", tier: str = "") -> dict[str, Any]:
    """Ficha de un anunciante (o del mercado entero): qué dice, cómo lo dice y cuánto dura."""
    classified = [it for it in items if it.get("cls")]
    days = [it["days_active"] for it in items if start_known(it)]

    def top(dist: list[dict[str, Any]]) -> dict[str, Any] | None:
        return dist[0] if dist else None

    fam = distribution(classified, _cls("angle_family"))
    hook = distribution(classified, _cls("hook_type"))
    fmt = distribution(classified, lambda it: it["format"])
    lon = longevity(items)
    return {
        "name": name,
        "slug": items[0].get("competitor_slug", "") if items and name else "",
        "tier": tier,
        "creatives": len(items),
        "active": len([it for it in items if it.get("status") == "active"]),
        "platforms": dict(Counter(it["platform"] for it in items)),
        "angle_family_dist": fam,
        "angle_dist": distribution(classified, _cls("primary_angle"))[:8],
        "hook_dist": hook,
        "format_dist": fmt,
        "funnel_dist": distribution(classified, _cls("funnel_stage")),
        "product_dist": distribution(classified, _cls("product")),
        "style_dist": distribution(classified, _cls("creative_style")),
        "top_family": top(fam),
        "top_hook": top(hook),
        "top_format": top(fmt),
        "promo": promo_summary(items),
        "longevity": {
            "buckets": lon["buckets"],
            "median_days": lon["median_days"],
            "max_days": max(days) if days else 0,
            "top_ids": lon["top_ids"][:4],
        },
    }


def competitor_profiles(market: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for it in market:
        by[it["competitor"]].append(it)
    profs = [profile(v, name=k, tier=v[0]["tier"]) for k, v in by.items()]
    return sorted(profs, key=lambda p: (0 if p["tier"] == "local" else 1, -p["creatives"], p["name"]))


def saturation(classified: list[dict[str, Any]]) -> dict[str, Any]:
    """Cuántos competidores usan cada mensaje/gancho: si 8 de 10 dicen lo mismo, está saturado."""
    comps = sorted({it["competitor"] for it in classified})
    out: dict[str, Any] = {"competitors": len(comps)}
    for key, getter in (("angle_family", _cls("angle_family")), ("hook", _cls("hook_type")), ("angle", _cls("primary_angle"))):
        users: dict[Any, set] = defaultdict(set)
        ads: Counter = Counter()
        for it in classified:
            v = getter(it)
            if v:
                users[v].add(it["competitor"])
                ads[v] += 1
        out[key] = sorted(
            ({"value": v, "competitors": len(u), "names": sorted(u), "ads": ads[v]} for v, u in users.items()),
            key=lambda d: (-d["competitors"], -d["ads"]),
        )
    return out


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
        "competitor_profiles": competitor_profiles(market),
        "market_profile": profile(market, name="Mercado"),
        "market_profile_weighted": {
            "angle_family_dist": weighted_distribution(classified, _cls("angle_family")),
            "hook_dist": weighted_distribution(classified, _cls("hook_type")),
            "format_dist": weighted_distribution(classified, lambda it: it["format"]),
            "funnel_dist": weighted_distribution(classified, _cls("funnel_stage")),
            "product_dist": weighted_distribution(classified, _cls("product")),
        },
        "tier_profiles": {t: profile([it for it in market if it["tier"] == t], name=t) for t in ("local", "global")},
        "own_profile": profile(own_items, name="own") if own_items else None,
        "saturation": saturation(classified),
        "ours_vs_market": {
            "has_own": bool(own_items),
            "angle_family": ours_vs_market(classified, [o for o in own_items if o.get("cls")], "angle_family"),
            "hook": ours_vs_market(classified, [o for o in own_items if o.get("cls")], "hook_type"),
            "product": ours_vs_market(classified, [o for o in own_items if o.get("cls")], "product"),
        },
    }
