"""Meta Ad Library vía navegador (Playwright). Camino para anuncios comerciales fuera de la UE.

Es scraping best-effort: el DOM de Facebook cambia sin aviso. Cada creativo queda marcado
con source='meta_web' y el checkpoint de Data QA lo señala para validación humana.
Requiere: pip install playwright && playwright install chromium
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from ..config import CompetitorConfig, settings
from .base import RawCreative

START_RE = re.compile(r"(?:Started running on|Comenzó a publicarse el|En circulación desde el)\s+(.+?)(?:\n|$)", re.I)
ID_RE = re.compile(r"(?:Library ID|Identificador de la biblioteca|ID de la biblioteca)[:\s]+(\d+)", re.I)
MONTHS = {
    "jan": 1, "ene": 1, "feb": 2, "mar": 3, "apr": 4, "abr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dec": 12, "dic": 12,
}


def parse_human_date(text: str) -> dt.date | None:
    """'Sep 12, 2026' / '12 sep 2026' / '12 de septiembre de 2026' -> date."""
    t = text.lower().replace(",", " ").replace(" de ", " ")
    nums = re.findall(r"\d+", t)
    mon = next((MONTHS[k] for k in MONTHS if re.search(rf"\b{k}", t)), None)
    if not mon or len(nums) < 2:
        return None
    day = int(nums[0]) if int(nums[0]) <= 31 else int(nums[1])
    year = int(nums[-1])
    try:
        return dt.date(year, mon, day)
    except ValueError:
        return None


class MetaAdLibraryWebCollector:
    name = "meta_web"
    platform = "meta"

    def __init__(self, headless: bool = True, max_cards: int = 200, screenshots: bool = True):
        self.headless = headless
        self.max_cards = max_cards
        self.screenshots = screenshots

    def supports(self, competitor: CompetitorConfig) -> bool:
        return bool(competitor.meta_page_id)

    def url_for(self, competitor: CompetitorConfig, country: str) -> str:
        return (
            "https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
            f"&country={country}&view_all_page_id={competitor.meta_page_id}&media_type=all&search_type=page"
        )

    def collect(self, competitor: CompetitorConfig) -> list[RawCreative]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Instalar playwright: pip install playwright && playwright install chromium") from exc

        out: list[RawCreative] = []
        assets = settings.assets_dir / "screenshots"
        assets.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page(viewport={"width": 1400, "height": 1000}, locale="es-UY")
            for country in competitor.ad_countries:
                page.goto(self.url_for(competitor, country), wait_until="domcontentloaded", timeout=90_000)
                page.wait_for_timeout(4000)
                self._scroll(page)
                cards = page.locator("div[class*='x1plvlek'] >> xpath=ancestor::div[contains(@class,'xh8yej3')][1]")
                if cards.count() == 0:
                    cards = page.locator("div:has-text('Library ID'), div:has-text('Identificador de la biblioteca')").locator("xpath=ancestor::div[1]")
                count = min(cards.count(), self.max_cards)
                for i in range(count):
                    card = cards.nth(i)
                    try:
                        text = card.inner_text(timeout=5000)
                    except Exception:
                        continue
                    ext_id = (ID_RE.search(text) or [None, ""])[1] if ID_RE.search(text) else ""
                    if not ext_id:
                        continue
                    start = START_RE.search(text)
                    imgs = card.locator("img").evaluate_all("els => els.map(e => e.src)")
                    vids = card.locator("video").evaluate_all("els => els.map(e => e.src || (e.querySelector('source')||{}).src)")
                    shot = ""
                    if self.screenshots:
                        shot_path = assets / f"meta_{ext_id}.png"
                        try:
                            card.screenshot(path=str(shot_path))
                            shot = str(shot_path)
                        except Exception:
                            shot = ""
                    body = _extract_body(text)
                    out.append(
                        RawCreative(
                            competitor_slug=competitor.slug,
                            platform="meta",
                            source="meta_web",
                            external_id=ext_id,
                            format="video" if any(vids) else "imagen" if any(imgs) else "desconocido",
                            placements=["feed"],
                            body_text=body,
                            media_urls=[u for u in (vids or imgs) if u],
                            thumbnail_url=next((u for u in imgs if u), ""),
                            snapshot_url=f"https://www.facebook.com/ads/library/?id={ext_id}",
                            countries=[country],
                            first_seen=parse_human_date(start.group(1)) if start else None,
                            is_active=True,
                            raw={"card_text": text, "screenshot": shot},
                        )
                    )
            browser.close()
        return out

    @staticmethod
    def _scroll(page, rounds: int = 8) -> None:
        for _ in range(rounds):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1500)


def _extract_body(card_text: str) -> str:
    """Se queda con el texto del anuncio, quitando metadatos de la tarjeta."""
    lines = [l.strip() for l in card_text.splitlines() if l.strip()]
    keep: list[str] = []
    skip = re.compile(r"(Library ID|Identificador|Started running|Comenzó a publicarse|En circulación|Active|Activo|Platforms|Plataformas|See ad details|Ver detalles|Sponsored|Publicidad|Open Dropdown)", re.I)
    for l in lines:
        if skip.search(l):
            continue
        keep.append(l)
    return "\n".join(keep)[:4000]


__all__ = ["MetaAdLibraryWebCollector", "parse_human_date"]
_ = Path  # mantener import para type hints externos
