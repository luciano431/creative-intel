"""Google Ads Transparency Center vía navegador (Playwright). Best-effort, sin costo.

Recorre https://adstransparency.google.com/advertiser/{id}?region={XX}, hace scroll y
captura las tarjetas de creativos (texto e imagen; los videos quedan como link a YouTube).
"""
from __future__ import annotations

import re

from ..config import CompetitorConfig, settings
from .base import RawCreative


class GoogleTransparencyWebCollector:
    name = "google_web"
    platform = "google"

    def __init__(self, headless: bool = True, max_cards: int = 200):
        self.headless = headless
        self.max_cards = max_cards

    def supports(self, competitor: CompetitorConfig) -> bool:
        return bool(competitor.google_advertiser_id)

    def collect(self, competitor: CompetitorConfig) -> list[RawCreative]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Instalar playwright: pip install playwright && playwright install chromium") from exc

        out: list[RawCreative] = []
        shots = settings.assets_dir / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            for country in competitor.ad_countries:
                page.goto(
                    f"https://adstransparency.google.com/advertiser/{competitor.google_advertiser_id}?region={country}",
                    wait_until="networkidle",
                    timeout=90_000,
                )
                for _ in range(8):
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(1500)
                cards = page.locator("creative-preview, [class*='creative-preview'], a[href*='/creative/']")
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
