"""Familias creativas: agrupa anuncios de un competidor que comparten producto + ángulo + hook.

Es una heurística determinística (no IA) para que sea auditable: dos anuncios del mismo
competidor que venden el mismo producto con el mismo ángulo y el mismo tipo de hook se
consideran variantes de un mismo concepto.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def concept_key(item: dict[str, Any]) -> str | None:
    cls = item.get("cls") or {}
    if not cls.get("primary_angle"):
        return None
    return "|".join([item["competitor_slug"], cls.get("product") or "?", cls.get("primary_angle") or "?", cls.get("hook_type") or "?"])


def group_concepts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        k = concept_key(it)
        if k:
            groups[k].append(it)
    out = []
    for key, members in groups.items():
        slug, product, angle, hook = key.split("|")
        firsts = sorted(m["first_seen"] for m in members)
        lasts = sorted(m["last_seen"] for m in members)
        out.append(
            {
                "key": key,
                "competitor": members[0]["competitor"],
                "competitor_slug": slug,
                "name": f"{product} · {angle} · hook {hook}",
                "product": product,
                "angle": angle,
                "hook_type": hook,
                "variants": len(members),
                "formats": sorted({m["format"] for m in members}),
                "platforms": sorted({m["platform"] for m in members}),
                "first_seen": firsts[0],
                "last_seen": lasts[-1],
                "creative_ids": [m["id"] for m in members],
                "example_headline": next((m["headline"] or m["body_text"][:120] for m in members if (m["headline"] or m["body_text"])), ""),
            }
        )
    return sorted(out, key=lambda c: (-c["variants"], c["competitor"]))
