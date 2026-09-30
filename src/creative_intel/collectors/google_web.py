"""Google Ads Transparency Center vía navegador (Playwright). Best-effort, sin costo.

Dos modos:
- por anunciante: https://adstransparency.google.com/advertiser/{AR...}?region={XX}
- por dominio (cuando no hay ID): https://adstransparency.google.com/?region={XX}&domain={sitio}
  El dominio sale de `google_domain` o, si no está, del `website` del competidor.
Hace scroll y captura las tarjetas de creativos (texto, imagen y video).
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from ..config import CompetitorConfig, settings
from .base import RawCreative


def domain_of(competitor: CompetitorConfig) -> str:
    raw = getattr(competitor, "google_domain", "") or competitor.website or ""
    if not raw:
        return ""
    host = urlparse(raw if "://" in raw else "https://" + raw).netloc or raw
    return host.lower().removeprefix("www.")


def region_param(country: str) -> str:
    return "anywhere" if country.upper() == "ALL" else country.upper()


class GoogleTransparencyWebCollector:
    name = "google_web"
    platform = "google"

    def __init__(self, headless: bool = True, max_cards: int = 200):
        self.headless = headless
        self.max_cards = max_cards

    def supports(self, competitor: CompetitorConfig) -> bool:
        return bool(competitor.google_advertiser_id or domain_of(competitor))

    def url_for(self, competitor: CompetitorConfig, country: str) -> str:
        region = region_param(country)
        if competitor.google_advertiser_id:
            return f"https://adstransparency.google.com/advertiser/{competitor.google_advertiser_id}?region={region}"
        return f"https://adstransparency.google.com/?region={region}&domain={domain_of(competitor)}"

    def collect(self, competitor: CompetitorConfig) -> list[RawCreative]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Instalar playwright: pip install playwright && playwright install chromium") from exc

        out: list[RawCreative] = []
        shots = settings.assets_dir / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless, executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_PATH") or None)
            page = browser.new_page(viewport={"width": 1400, "height": 1000}, locale="es-UY")
            for country in competitor.ad_countries:
                page.goto(self.url_for(competitor, country), wait_until="domcontentloaded", timeout=90_000)
                page.wait_for_timeout(5000)
                for _ in range(8):
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(1500)
                cards = page.locator("creative-preview, [class*='creative-preview'], a[href*='/creative/']")
                if cards.count() == 0:
                    dbg = settings.assets_dir / "debug"
                    dbg.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(dbg / f"google_{competitor.slug}_{country}.png"), full_page=True)
                    (dbg / f"google_{competitor.slug}_{country}.html").write_text(page.content(), encoding="utf-8")
                    print(f"[google_web] 0 tarjetas para {competitor.slug} ({country}); diagnóstico guardado en {dbg}")
                for i in range(min(cards.count(), self.max_cards)):
                    card = cards.nth(i)
                    try:
                        href = card.get_attribute("href") or card.locator("a").first.get_attribute("href") or ""
                        text = card.inner_text(timeout=4000)
                    except Exception:
                        continue
                    m = re.search(r"/creative/(CR\w+)", href or "")
                    ext_id = m.group(1) if m else ""
                    if not ext_id:
                        continue
                    imgs = card.locator("img").evaluate_all("els => els.map(e => e.src)")
                    fmt = "imagen" if imgs else "texto"
                    if "youtube" in (text or "").lower() or card.locator("iframe").count():
                        fmt = "video"
                    shot = shots / f"google_{ext_id}.png"
                    try:
                        card.screenshot(path=str(shot))
                    except Exception:
                        shot = None  # type: ignore[assignment]
                    out.append(
                        RawCreative(
                            competitor_slug=competitor.slug,
                            platform="google",
                            source="google_web",
                            external_id=ext_id,
                            format=fmt,
                            placements=["search"] if fmt == "texto" else ["youtube"] if fmt == "video" else ["display"],
                            body_text=(text or "")[:4000],
                            media_urls=[u for u in imgs if u],
                            thumbnail_url=next((u for u in imgs if u), ""),
                            snapshot_url=("https://adstransparency.google.com" + href) if href.startswith("/") else href,
                            countries=[country],
                            is_active=True,
                            raw={"card_text": text, "screenshot": str(shot) if shot else ""},
                        )
                    )
            browser.close()
        return out
