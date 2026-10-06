"""Registro de colectores. Agregar una fuente = agregar una clase acá."""
from __future__ import annotations

from .base import Collector, RawCreative
from .google_serpapi import GoogleSerpApiCollector
from .google_web import GoogleTransparencyWebCollector
from .manual import ManualCollector, load_manual_file
from .meta_api import MetaAdLibraryApiCollector
from .meta_insights import MetaInsightsCollector
from .meta_web import MetaAdLibraryWebCollector

REGISTRY: dict[str, type] = {
    "meta_api": MetaAdLibraryApiCollector,
    "meta_web": MetaAdLibraryWebCollector,
    "google_serpapi": GoogleSerpApiCollector,
    "google_web": GoogleTransparencyWebCollector,
    "manual": ManualCollector,
    "meta_insights": MetaInsightsCollector,
}


def get_collectors(names: list[str] | None = None) -> list[Collector]:
    import os

    names = names or [n.strip() for n in os.getenv("COLLECT_SOURCES", "meta_web,google_web,manual").split(",") if n.strip()]
    # Con token de Meta, siempre se traen los resultados propios del cliente (si tiene cuenta publicitaria cargada)
    if os.getenv("META_ACCESS_TOKEN") and "meta_insights" not in names:
        names = [*names, "meta_insights"]
    return [REGISTRY[n]() for n in names]


__all__ = ["Collector", "RawCreative", "REGISTRY", "get_collectors", "load_manual_file"]
