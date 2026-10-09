"""Cómo están hechos los anuncios (ejecución) y qué ya hace el cliente.

Más allá del ángulo: si aparece una persona hablando a cámara, si dicen el precio, a dónde manda el CTA,
a quién le hablan y cómo se resuelven visualmente. Comparar al cliente contra las referencias en estas
dimensiones da los huecos de ejecución ("nunca muestran una persona", "nadie dice el precio").
"""
from __future__ import annotations

from collections import Counter
from typing import Any

PEOPLE = {"persona_a_camara": "persona hablando a cámara", "persona_sin_hablar": "persona sin hablar", "sin_personas": "sin personas"}
CTA = {"whatsapp": "WhatsApp", "web": "web", "mensaje_directo": "mensaje directo", "llamada": "llamada", "tienda_fisica": "tienda física",
       "formulario": "formulario", "ninguno": "sin CTA"}
EXECUTION = {"placa_grafica": "placa gráfica", "foto_producto": "foto de producto", "foto_lifestyle": "foto de situación",
             "video_persona": "video con persona", "video_producto": "video de producto", "ugc": "estilo UGC (celular)", "texto": "texto", "otro": "otro"}


def _dist(values: list[str], labels: dict[str, str]) -> list[dict[str, Any]]:
    c = Counter(v for v in values if v)
    n = sum(c.values()) or 1
    return [{"value": k, "label": labels.get(k, k), "count": v, "pct": round(100 * v / n)} for k, v in c.most_common()]


def execution_profile(items: list[dict[str, Any]]) -> dict[str, Any]:
    cls = [it.get("cls") or {} for it in items]
    n = len(items)
    talks = Counter((c.get("talks_to") or "").strip().lower() for c in cls if (c.get("talks_to") or "").strip())
    return {
        "n": n,
        "people": _dist([c.get("people") for c in cls], PEOPLE),
        "on_camera": sum(c.get("people") == "persona_a_camara" for c in cls),
        "price": sum(bool(c.get("shows_price")) for c in cls),
        "whatsapp": sum(c.get("cta_channel") == "whatsapp" for c in cls),
        "cta": _dist([c.get("cta_channel") for c in cls], CTA),
        "execution": _dist([c.get("execution") for c in cls], EXECUTION),
        "talks_to": [{"value": k, "count": v} for k, v in talks.most_common(8)],
    }


def own_audit(own: list[dict[str, Any]]) -> dict[str, Any]:
    """Inventario de lo que el cliente ya tiene en el aire: ángulos ocupados (con un ejemplo textual) y su ejecución."""
    active = [it for it in own if it.get("source") != "meta_insights"]  # lo que está en la Biblioteca, no las métricas
    by_angle: dict[str, list[dict[str, Any]]] = {}
    for it in active:
        a = (it.get("cls") or {}).get("primary_angle")
        if a:
            by_angle.setdefault(a, []).append(it)
    angles = []
    for a, its in sorted(by_angle.items(), key=lambda kv: -len(kv[1])):
        ex = its[0]
        angles.append({"angle": a, "family": (ex.get("cls") or {}).get("angle_family"), "count": len(its),
                       "example": (ex.get("cls") or {}).get("copy_quote") or ex.get("headline") or "", "example_id": ex["id"]})
    return {"active": len(active), "platforms": dict(Counter(it["platform"] for it in active)), "angles": angles,
            "profile": execution_profile(active)}


def execution_gaps(own: dict[str, Any], refs: dict[str, Any]) -> list[dict[str, Any]]:
    """Diferencias de ejecución entre el cliente y las referencias, como hechos con sus conteos."""
    if not own.get("n") or not refs.get("n"):
        return []
    rows = []
    for key, label in (("on_camera", "Persona hablando a cámara"), ("price", "Dicen el precio"), ("whatsapp", "CTA a WhatsApp")):
        o, r = own[key] / own["n"], refs[key] / refs["n"]
        rows.append({"dimension": label, "own": f"{own[key]} de {own['n']}", "refs": f"{refs[key]} de {refs['n']}",
                     "own_pct": round(100 * o), "refs_pct": round(100 * r), "gap": round(100 * (r - o))})
    top_own = (own["execution"] or [{}])[0]
    if top_own.get("pct", 0) >= 60:
        rows.append({"dimension": f"Resolución dominante: {top_own['label']}", "own": f"{top_own['count']} de {own['n']}",
                     "refs": next((f"{e['count']} de {refs['n']}" for e in refs["execution"] if e["value"] == top_own["value"]), f"0 de {refs['n']}"),
                     "own_pct": top_own["pct"], "refs_pct": next((e["pct"] for e in refs["execution"] if e["value"] == top_own["value"]), 0),
                     "gap": 0})
    return sorted(rows, key=lambda r: -abs(r["gap"]))
