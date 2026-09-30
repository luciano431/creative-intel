"""Meta Ad Library vía Graph API oficial (endpoint ads_archive).

LIMITACIÓN IMPORTANTE: la API devuelve TODOS los anuncios solo para países de la UE/UK
(obligación DSA) y anuncios políticos/sociales para el resto del mundo. Para anuncios
comerciales en Uruguay la API no devuelve resultados: usar `meta_web` (scraping) o `manual`.
Sí sirve para los competidores globales observados en ES/FR/DE/IT.
"""
from __future__ import annotations

from typing import Any

import httpx

from ..config import CompetitorConfig, settings
from .base import RawCreative, parse_date

FIELDS = ",".join(
    [
        "id",
        "page_id",
        "page_name",
        "ad_creative_bodies",
        "ad_creative_link_titles",
        "ad_creative_link_descriptions",
        "ad_creative_link_captions",
        "ad_delivery_start_time",
        "ad_delivery_stop_time",
        "ad_snapshot_url",
        "publisher_platforms",
        "languages",
        "target_ages",
        "target_gender",
        "eu_total_reach",
    ]
)


class MetaAdLibraryApiCollector:
    name = "meta_api"
    platform = "meta"

    def __init__(self, token: str | None = None, graph_version: str | None = None, client: httpx.Client | None = None):
        self.token = token or settings.meta_access_token
        self.graph_version = graph_version or settings.meta_graph_version
        self.client = client or httpx.Client(timeout=60)

    def supports(self, competitor: CompetitorConfig) -> bool:
        return bool(self.token and competitor.meta_page_id)

    def collect(self, competitor: CompetitorConfig) -> list[RawCreative]:
        url = f"https://graph.facebook.com/{self.graph_version}/ads_archive"
        params: dict[str, Any] = {
            "access_token": self.token,
            "search_page_ids": competitor.meta_page_id,
            "ad_reached_countries": "[" + ",".join(f'"{c}"' for c in competitor.ad_countries) + "]",
            "ad_active_status": "ALL",
            "ad_type": "ALL",
            "fields": FIELDS,
            "limit": 100,
        }
        out: list[RawCreative] = []
        while url:
            resp = self.client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
            for ad in payload.get("data", []):
                out.append(self.to_raw(ad, competitor))
            url = (payload.get("paging") or {}).get("next")
            params = {}  # `next` ya trae los parámetros
        return out

    @staticmethod
    def to_raw(ad: dict[str, Any], competitor: CompetitorConfig) -> RawCreative:
        stop = ad.get("ad_delivery_stop_time")
        platforms = [p.lower() for p in ad.get("publisher_platforms", [])]
        return RawCreative(
            competitor_slug=competitor.slug,
            platform="meta",
            source="meta_api",
            external_id=str(ad.get("id", "")),
            format="desconocido",  # la API no expone el tipo de medio; se infiere en clasificación
            placements=sorted({"feed" if p in ("facebook", "instagram") else p for p in platforms}) or ["desconocido"],
            headline=" | ".join(ad.get("ad_creative_link_titles", []) or []),
            body_text="\n".join(ad.get("ad_creative_bodies", []) or []),
            description=" | ".join(ad.get("ad_creative_link_descriptions", []) or []),
            cta_text="",
            landing_url=" | ".join(ad.get("ad_creative_link_captions", []) or []),
            snapshot_url=ad.get("ad_snapshot_url", ""),
            countries=list(competitor.ad_countries),
            engagement={"eu_total_reach": ad.get("eu_total_reach")} if ad.get("eu_total_reach") else {},
            first_seen=parse_date(ad.get("ad_delivery_start_time")),
            last_seen=parse_date(stop),
            is_active=stop is None,
            raw=ad,
        )
