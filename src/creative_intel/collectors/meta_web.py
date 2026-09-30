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
                    img_info = card.locator("img").evaluate_all("els => els.map(e => ({src: e.src, w: e.naturalWidth || e.width, h: e.naturalHeight || e.height}))")
                    imgs = [i["src"] for i in sorted(img_info, key=lambda i: -(i["w"] * i["h"])) if i["src"] and i["w"] >= 150 and i["h"] >= 150]
                    vids = card.locator("video").evaluate_all("els => els.map(e => e.src || (e.querySelector('source')||{}).src)")
                    shot = ""
                    if self.screenshots:
                        shot_path = assets / f"meta_{ext_id}.png"
                        try:
                            card.screenshot(path=str(shot_path))
                            shot = str(shot_path)
                        except Exception:
                            shot = ""
                    parsed = parse_card_text(text, competitor.name)
                    out.append(
                        RawCreative(
                            competitor_slug=competitor.slug,
                            platform="meta",
                            source="meta_web",
                            external_id=ext_id,
                            format="video" if any(vids) else "imagen" if any(imgs) else "desconocido",
                            placements=["feed"],
                            headline=parsed["headline"],
                            body_text=parsed["body"],
                            description=parsed["description"],
                            cta_text=parsed["cta"],
                            landing_url=parsed["landing"],
                            media_urls=[u for u in (vids or imgs) if u],
                            thumbnail_url=next((u for u in imgs if u), ""),
                            snapshot_url=f"https://www.facebook.com/ads/library/?id={ext_id}",
                            countries=[country],
                            first_seen=parse_human_date(start.group(1)) if start else None,
                            is_active=True,
                            raw={"card_text": text, "screenshot": shot, "multiple_versions": parsed["multiple_versions"]},
                        )
                    )
            browser.close()
        return out

    @staticmethod
    def _scroll(page, rounds: int = 8) -> None:
        for _ in range(rounds):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1500)


UI_NOISE = re.compile(
    r"^(Library ID|Identificador de la biblioteca|ID de la biblioteca|Started running|Comenzó a publicarse|En circulación"
    r"|Activo$|Active$|Inactivo$|Inactive$|Plataformas|Platforms|Ver detalles|See ad details|Ver resumen|See summary"
    r"|Patrocinado$|Publicidad$|Sponsored$|Abrir menú|Open Drop|Este anuncio tiene varias versiones|This ad has multiple versions"
    r"|Detalles del anuncio|Ad details|Categor[ií]a|Category)",
    re.I,
)
CTA_LABELS = {
    "más información", "mas informacion", "más info", "cotizar", "cotizá", "cotiza", "contratar", "contratá", "comprar", "comprá",
    "registrarte", "registrarse", "descargar", "enviar mensaje", "obtener oferta", "reservar", "solicitar", "ver más", "ver mas",
    "learn more", "sign up", "get quote", "get offer", "shop now", "contact us", "apply now", "download", "book now", "send message",
    "whatsapp", "enviar mensaje de whatsapp", "llamar ahora", "call now", "suscribirte", "subscribe",
}
DOMAIN_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}(/\S*)?$", re.I)


def parse_card_text(card_text: str, advertiser_name: str = "") -> dict:
    """Separa el texto de una tarjeta de Ad Library en body / landing / headline / description / cta.

    Orden típico de la tarjeta: metadatos → nombre de página → texto principal → [imagen] →
    dominio de la landing → titular → descripción → botón CTA.
    """
    names = {n.lower() for n in [advertiser_name or ""] + re.findall(r"\(([^)]+)\)", advertiser_name or "") if n}
    lines: list[str] = []
    multiple = False
    skip_next = False  # la línea que sigue a "Ver detalles del anuncio" es el nombre de la página
    for raw in card_text.splitlines():
        l = raw.strip()
        if not l or not re.search(r"[\wáéíóúñ]", l, re.I):
            continue
        if re.search(r"varias versiones|multiple versions", l, re.I):
            multiple = True
        if re.match(r"(Ver detalles|See ad details)", l, re.I):
            skip_next = True
            continue
        if UI_NOISE.search(l):
            continue
        if skip_next:
            skip_next = False
            continue
        if any(n in l.lower() and len(l) <= len(n) + 15 for n in names):
            continue  # nombre de la página
        lines.append(l)
    cta = ""
    if lines and lines[-1].lower().strip("¡!.") in CTA_LABELS:
        cta = lines.pop()
    landing, headline, description = "", "", ""
    for i, l in enumerate(lines):
        if DOMAIN_RE.match(l) and " " not in l:
            landing = l.lower()
            headline = lines[i + 1] if i + 1 < len(lines) else ""
            description = " ".join(lines[i + 2 :])
            lines = lines[:i]
            break
    if not headline and cta and len(lines) >= 2 and len(lines[-1]) <= 100:
        # Sin dominio visible: el titular del enlace es la línea corta que precede al botón CTA
        headline = lines.pop()
    body = "\n".join(lines)[:4000]
    return {"body": body, "landing": landing, "headline": headline, "description": description[:1000], "cta": cta, "multiple_versions": multiple}


def _extract_body(card_text: str) -> str:  # compat
    return parse_card_text(card_text)["body"]


__all__ = ["MetaAdLibraryWebCollector", "parse_human_date", "parse_card_text"]
_ = Path  # mantener import para type hints externos
