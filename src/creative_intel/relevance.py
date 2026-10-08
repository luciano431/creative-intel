"""Filtro de rubro: de las marcas generalistas (Sodimac, Mercado Libre...) y de los anunciantes encontrados por
palabra clave, el Radar se queda solo con los anuncios de lo que vende el cliente.

Las palabras salen de `client.focus_keywords` en competitors.yaml. Se compara sin tildes ni mayúsculas y por
comienzo de palabra ("parrill" encuentra parrilla, parrillero, parrillas). Con "=" adelante la palabra tiene que
ser exacta ("=cama" no encuentra "cámara").
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def matches(text: str, keywords: Iterable[str]) -> bool:
    kws = [fold(k).strip() for k in keywords if k and k.strip()]
    if not kws:
        return True
    t = fold(text)
    return any(re.search(r"(?<![a-z0-9])" + (re.escape(k[1:]) + r"(?![a-z0-9])" if k.startswith("=") else re.escape(k)), t)
               for k in kws)


def item_text(it: dict[str, Any]) -> str:
    """Todo lo que dice el anuncio: copy y, si ya se clasificó, lo que la IA leyó de la imagen o el video."""
    cls = it.get("cls") or {}
    parts = [it.get("headline") or "", it.get("body_text") or "", cls.get("summary") or "", cls.get("promise") or "",
             cls.get("offer") or "", " ".join(cls.get("key_claims") or []), str((it.get("raw") or {}).get("text_in_image") or "")]
    return " ".join(parts)


def needs_filter(it: dict[str, Any], generalists: set[str]) -> bool:
    return it.get("tier") == "mercado" or it.get("competitor_slug") in generalists


def off_topic(it: dict[str, Any]) -> bool:
    """La IA vio el anuncio (imagen y texto) y dijo que el producto no es de ninguna categoría del cliente."""
    return it.get("tier") != "own" and (it.get("cls") or {}).get("product") == "otro_rubro"


def filter_items(items: list[dict[str, Any]], keywords: list[str], generalists: set[str]) -> list[dict[str, Any]]:
    items = [it for it in items if not off_topic(it)]
    if not keywords:
        return items
    return [it for it in items if not needs_filter(it, generalists) or matches(item_text(it), keywords)]


def client_filter() -> tuple[list[str], set[str]]:
    """Palabras del rubro del cliente activo y las marcas generalistas a filtrar."""
    from .config import load_client, load_competitors

    return load_client().focus_keywords, {c.slug for c in load_competitors() if c.generalist}
