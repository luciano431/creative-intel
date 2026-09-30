"""Google Ads Transparency Center vía SerpApi (engine google_ads_transparency_center).

Google no ofrece API oficial para el Transparency Center. SerpApi expone el listado de
creativos de un anunciante (texto, imagen, video), con formato y fechas. Es la vía
recomendada para producción: estable y barata. Alternativa sin costo: `google_web`.
"""
from __future__ import annotations

from typing import Any

import httpx

from ..config import CompetitorConfig, settings
from .base import RawCreative, parse_date

FORMAT_MAP = {"text": "texto", "image": "imagen", "video": "video"}


class GoogleSerpApiCollector:
    name = "google_serpapi"
    platform = "google"

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None):
        self.api_key = api_key or settings.serpapi_key
        self.client = client or httpx.Client(timeout=60)

    def supports(self, competitor: CompetitorConfig) -> bool:
        return bool(self.api_key and competitor.google_advertiser_id)

    def collect(self, competitor: CompetitorConfig) -> list[RawCreative]:
        out: list[RawCreative] = []
        for country in competitor.ad_countries:
            params: dict[str, Any] = {
                "engine": "google_ads_transparency_center",
                "advertiser_id": competitor.google_advertiser_id,
                "region": country_code_to_region(country),
                "api_key": self.api_key,
                "num": 100,
            }
            next_token = None
            while True:
                if next_token:
                    params["next_page_token"] = next_token
                resp = self.client.get("https://serpapi.com/search.json", params=params)
                resp.raise_for_status()
                payload = resp.json()
                for ad in payload.get("ad_creatives", []):
                    out.append(self.to_raw(ad, competitor, country))
                next_token = (payload.get("serpapi_pagination") or {}).get("next_page_token")
                if not next_token:
                    break
        return out

    @staticmethod
    def to_raw(ad: dict[str, Any], competitor: CompetitorConfig, country: str) -> RawCreative:
        fmt = FORMAT_MAP.get(str(ad.get("format", "")).lower(), "desconocido")
        last = parse_date(ad.get("last_shown"))
        return RawCreative(
            competitor_slug=competitor.slug,
            platform="google",
            source="google_serpapi",
            external_id=str(ad.get("ad_id") or ad.get("creative_id") or ""),
            format=fmt,
            placements=["search"] if fmt == "texto" else ["youtube"] if fmt == "video" else ["display"],
            headline=ad.get("title", "") or "",
            body_text=ad.get("text", "") or ad.get("description", "") or "",
            landing_url=ad.get("target_domain", "") or "",
            media_urls=[u for u in (ad.get("image"), ad.get("video")) if u],
            thumbnail_url=ad.get("image", "") or "",
            snapshot_url=ad.get("details_link", "") or ad.get("link", "") or "",
            countries=[country],
            first_seen=parse_date(ad.get("first_shown")),
            last_seen=last,
            is_active=True,
            raw=ad,
        )


def country_code_to_region(code: str) -> str:
    """SerpApi acepta códigos ISO-2 numéricos o textuales según versión; mantenemos ISO-2."""
    return code.upper()
