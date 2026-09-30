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
        debug = bool(os.getenv("CREATIVE_INTEL_DEBUG"))
        dbg = settings.assets_dir / "debug"
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless, executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_PATH") or None)
            page = browser.new_page(viewport={"width": 1400, "height": 1000}, locale="es-UY")
            for country in competitor.ad_countries:
                page.goto(self.url_for(competitor, country), wait_until="domcontentloaded", timeout=90_000)
                page.wait_for_timeout(5000)
                for _ in range(8):
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(1500)
                hrefs: list[str] = []
                for h in page.locator("a[href*='/creative/']").evaluate_all("els => els.map(e => e.getAttribute('href'))"):
                    if h and h not in hrefs:
                        hrefs.append(h)
                if not hrefs:
                    dbg.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(dbg / f"google_{competitor.slug}_{country}.png"), full_page=True)
                    (dbg / f"google_{competitor.slug}_{country}.html").write_text(page.content(), encoding="utf-8")
                    print(f"[google_web] 0 tarjetas para {competitor.slug} ({country}); diagnóstico guardado en {dbg}")
                    continue
                for href in hrefs[: self.max_cards]:
                    m = re.search(r"/creative/(CR\w+)", href)
                    if not m:
                        continue
                    ext_id = m.group(1)
                    url = ("https://adstransparency.google.com" + href) if href.startswith("/") else href
                    try:
                        detail = self._detail(page, url, ext_id, shots, debug, dbg, competitor.slug)
                    except Exception as exc:
                        print(f"[google_web] detalle {ext_id} falló: {exc}")
                        continue
                    out.append(
                        RawCreative(
                            competitor_slug=competitor.slug,
                            platform="google",
                            source="google_web",
                            external_id=ext_id,
                            format=detail["format"],
                            placements=["search"] if detail["format"] == "texto" else ["youtube"] if detail["format"] == "video" else ["display"],
                            headline=detail["headline"],
                            body_text=detail["body"],
                            landing_url=detail["landing"],
                            media_urls=detail["media"],
                            thumbnail_url=detail["media"][0] if detail["media"] and detail["format"] == "imagen" else "",
                            snapshot_url=url,
                            countries=[] if country.upper() == "ALL" else [country],
                            first_seen=detail["first_seen"],
                            last_seen=detail["last_seen"],
                            is_active=True,
                            raw={"page_text": detail["page_text"][:3000], "screenshot": detail["screenshot"], "frame_texts": detail["frame_texts"][:5]},
                        )
                    )
            browser.close()
        return out

    def _detail(self, page, url: str, ext_id: str, shots, debug: bool, dbg, slug: str) -> dict:
        """Abre la página de detalle de un creativo y extrae contenido, fechas y captura."""
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(4000)
        page_text = page.inner_text("body")
        # Anuncios de texto/imagen se renderizan dentro de iframes
        frame_texts: list[str] = []
        media: list[str] = []
        is_video = False
        for fr in page.frames:
            if fr == page.main_frame:
                continue
            furl = fr.url or ""
            if "youtube" in furl:
                is_video = True
                media.append(furl)
                continue
            try:
                t = fr.inner_text("body", timeout=3000).strip()
                if t:
                    frame_texts.append(t)
                for im in fr.evaluate("() => Array.from(document.images).map(i => ({src: i.src, w: i.naturalWidth, h: i.naturalHeight}))"):
                    if im["src"] and im["w"] >= 100 and im["h"] >= 100:
                        media.append(im["src"])
            except Exception:
                continue
        for im in page.evaluate("() => Array.from(document.images).map(i => ({src: i.src, w: i.naturalWidth, h: i.naturalHeight}))"):
            if im["src"] and im["w"] >= 200 and im["h"] >= 100 and "gstatic" not in im["src"]:
                media.append(im["src"])
        if page.locator("video").count():
            is_video = True
        low = page_text.lower()
        if is_video or re.search(r"\bv[ií]deo\b", low):
            fmt = "video"
        elif re.search(r"\btexto\b|\btext\b", low) and not media:
            fmt = "texto"
        elif media or re.search(r"\bimagen\b|\bimage\b", low):
            fmt = "imagen"
        else:
            fmt = "texto" if frame_texts else "desconocido"
        text_lines = [l.strip() for t in frame_texts for l in t.splitlines() if l.strip()]
        headline = text_lines[0] if text_lines else ""
        body = "\n".join(text_lines[1:])[:4000] if len(text_lines) > 1 else ""
        landing = ""
        for l in text_lines:
            if re.match(r"^(https?://)?[a-z0-9.-]+\.[a-z]{2,}(/\S*)?$", l, re.I) and " " not in l:
                landing = re.sub(r"^https?://", "", l.lower())
                break
        shot = shots / f"google_{ext_id}.png"
        try:
            page.screenshot(path=str(shot), full_page=False)
        except Exception:
            shot = None
        if debug:
            dbg.mkdir(parents=True, exist_ok=True)
            (dbg / f"google_detail_{slug}_{ext_id}.txt").write_text(page_text + "\n\n===== FRAMES =====\n" + "\n-----\n".join(frame_texts), encoding="utf-8")
        return {
            "format": fmt,
            "headline": headline,
            "body": body,
            "landing": landing,
            "media": list(dict.fromkeys(media)),
            "first_seen": _date_after(page_text, r"(primera vez|first shown|first served)"),
            "last_seen": _date_after(page_text, r"(última vez|ultima vez|last shown|last served)"),
            "page_text": page_text,
            "frame_texts": frame_texts,
            "screenshot": str(shot) if shot else "",
        }


def _date_after(text: str, keyword_re: str):
    from .meta_web import parse_human_date

    m = re.search(keyword_re + r"[^\n]{0,40}?(\d{1,2}\s+\S+\.?\s+\d{4}|\w{3,10}\.? \d{1,2},? \d{4})", text, re.I)
    return parse_human_date(m.group(2)) if m else None
