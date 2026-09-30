"""Registro de colectores. Agregar una fuente = agregar una clase acá."""
from __future__ import annotations

from .base import Collector, RawCreative
from .google_serpapi import GoogleSerpApiCollector
from .google_web import GoogleTransparencyWebCollector
from .manual import ManualCollector, load_manual_file
from .meta_api import MetaAdLibraryApiCollector
from .meta_web import MetaAdLibraryWebCollector

REGISTRY: dict[str, type] = {
    "meta_api": MetaAdLibraryApiCollector,
    "meta_web": MetaAdLibraryWebCollector,
    "google_serpapi": GoogleSerpApiCollector,
    "google_web": GoogleTransparencyWebCollector,
    "manual": ManualCollector,
}


def get_collectors(names: list[str] | None = None) -> list[Collector]:
    names = names or ["meta_api", "google_serpapi", "manual"]
    return [REGISTRY[n]() for n in names]


__all__ = ["Collector", "RawCreative", "REGISTRY", "get_collectors", "load_manual_file"]
