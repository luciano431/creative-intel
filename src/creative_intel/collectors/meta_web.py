"""Meta Ad Library vía navegador (Playwright). Camino para anuncios comerciales fuera de la UE.

Es scraping best-effort: el DOM de Facebook cambia sin aviso. Cada creativo queda marcado
con source='meta_web' y el checkpoint de Data QA lo señala para validación humana.
Requiere: pip install playwright && playwright install chromium
"""
from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

from ..config import CompetitorConfig, settings
from .base import RawCreative

START_RE = re.compile(r"(?:Started running on|Comenzó a publicarse el|En circulación desde el)\s+(.+?)(?:\n|$)", re.I)
# Anuncios finalizados: "3 nov 2025 - 5 nov 2025" / "Nov 3, 2025 - Nov 5, 2025"
_DATE_PART = r"(\d{1,2}\s+(?:de\s+)?[A-Za-zÁÉÍÓÚáéíóú]{3,10}\.?\s+(?:de\s+)?\d{4}|[A-Za-z]{3,10}\.?\s+\d{1,2},?\s+\d{4})"
RANGE_RE = re.compile(_DATE_PART + r"\s*[-–]\s*" + _DATE_PART)
NET_ERR_RE = re.compile(r"ERR_NAME_NOT_RESOLVED|ERR_INTERNET_DISCONNECTED|ERR_NETWORK_CHANGED|ERR_CONNECTION_(RESET|CLOSED|REFUSED|TIMED_OUT)|ERR_TIMED_OUT")
INACTIVE_RE = re.compile(r"^\s*(Inactivo|Inactive|Finalizado)\b", re.I | re.M)
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
    if not 2004 <= year <= dt.date.today().year + 1:  # año ilegible o mal leído: mejor sin fecha que una fecha falsa
        return None
    try:
        return dt.date(year, mon, day)
    except ValueError:
        return None


class MetaAdLibraryWebCollector:
    name = "meta_web"
    platform = "meta"

    def __init__(self, headless: bool = True, max_cards: int | None = None, screenshots: bool = True):
        self.headless = headless
        # Los N anuncios más vistos por marca y país (la Biblioteca se pide ordenada por impresiones)
        self.max_cards = max_cards or settings.top_ads_per_brand
        self.declared: dict[str, int] = {}  # slug -> anuncios que declara la Biblioteca (suma de países)
        self.screenshots = screenshots

    def supports(self, competitor: CompetitorConfig) -> bool:
        return bool(competitor.meta_page_id)

    @staticmethod
    def window_params(date_from=None, date_to=None) -> str:
        """Filtro por fecha de inicio del anuncio (para estudiar ediciones pasadas de un evento)."""
        out = ""
        if date_from:
            out += f"&start_date[min]={date_from.isoformat()}"
        if date_to:
            out += f"&start_date[max]={date_to.isoformat()}"
        return out

    def keyword_url(self, keyword: str, country: str, date_from=None, date_to=None) -> str:
        from urllib.parse import quote

        return (
            f"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country={country}"
            f"&q={quote(keyword)}&search_type=keyword_unordered&media_type=all"
            "&sort_data[direction]=desc&sort_data[mode]=total_impressions" + self.window_params(date_from, date_to)
        )

    def collect_window(self, competitor: CompetitorConfig, country: str, date_from, date_to, limit: int) -> list[RawCreative]:
        """Los anuncios más vistos de una marca que arrancaron dentro de una ventana de fechas (activos o finalizados).

        Sin fechas (modo en vivo): los activos hoy, empiecen cuando empiecen.
        """
        if date_from is None and date_to is None:
            url = self.url_for(competitor, country, status="active")
        else:
            url = self.url_for(competitor, country, status="all") + self.window_params(date_from, date_to)
        return self._collect_url(url, competitor.slug, competitor.name, country, limit, keywords=competitor.focus_keywords)

    def collect_keyword(self, keyword: str, country: str, date_from, date_to, limit: int,
                        focus: list[str] | None = None) -> list[RawCreative]:
        """Los anuncios más vistos de cualquier anunciante que mencionan una palabra (ej. 'cyberlunes') en una ventana.

        Con `focus`, solo los que además hablan del rubro del cliente.
        """
        return self._collect_url(self.keyword_url(keyword, country, date_from, date_to), None, "", country, limit, keywords=focus)

    def _collect_url(self, url: str, slug: str | None, name: str, country: str, limit: int,
                     keywords: list[str] | None = None) -> list[RawCreative]:
        """Con reintentos si se corta internet (sin conexión, DNS): 15 s, 45 s y 2 min."""
        import time

        for attempt, pause in enumerate((0, 15, 45, 120)):
            if pause:
                print(f"[meta_web] sin conexión; reintento {attempt}/3 de {name or 'búsqueda'} en {pause}s")
                time.sleep(pause)
            try:
                return self._collect_url_once(url, slug, name, country, limit, keywords)
            except Exception as exc:
                if not NET_ERR_RE.search(str(exc)) or attempt == 3:
                    raise
        return []

    def _collect_url_once(self, url: str, slug: str | None, name: str, country: str, limit: int,
                          keywords: list[str] | None = None) -> list[RawCreative]:
        from playwright.sync_api import sync_playwright

        out: list[RawCreative] = []
        assets = settings.assets_dir / "screenshots"
        assets.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless, executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_PATH") or None)
            page = browser.new_page(viewport={"width": 1400, "height": 1000}, locale="es-UY")
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(4000)
            # Con filtro de rubro hay que mirar más tarjetas para encontrar las que sirven
            self._scroll(page, rounds=max(2, limit // 4) * (4 if keywords else 1))
            fake = CompetitorConfig(slug=slug or "mercado", name=name, country=country, tier="local") if slug is not None else None
            self._take(page, fake, country, out, assets, limit=limit, market=slug is None, keywords=keywords)
            print(f"[meta_web] {name or 'búsqueda por palabra'} ({country}): {len(out)} anuncios en la ventana")
            if not out and os.getenv("CREATIVE_INTEL_DEBUG"):
                dbg = settings.assets_dir / "debug"
                dbg.mkdir(parents=True, exist_ok=True)
                tag = (slug or "keyword") + "_" + country
                page.screenshot(path=str(dbg / f"meta_window_{tag}.png"), full_page=True)
            browser.close()
        return out

    def url_for(self, competitor: CompetitorConfig, country: str, status: str = "active") -> str:
        return (
            f"https://www.facebook.com/ads/library/?active_status={status}&ad_type=all"
            f"&country={country}&view_all_page_id={competitor.meta_page_id}&media_type=all&search_type=page"
            "&sort_data[direction]=desc&sort_data[mode]=total_impressions"
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
            browser = p.chromium.launch(headless=self.headless, executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_PATH") or None)
            page = browser.new_page(viewport={"width": 1400, "height": 1000}, locale="es-UY")
            for country in competitor.ad_countries:
                page.goto(self.url_for(competitor, country), wait_until="domcontentloaded", timeout=90_000)
                page.wait_for_timeout(4000)
                total = declared_results(page.inner_text("body"))
                if total is not None:
                    self.declared[competitor.slug] = self.declared.get(competitor.slug, 0) + total
                focus = competitor.focus_keywords
                print(f"[meta_web] {competitor.slug} ({country}): ~{total if total is not None else '?'} anuncios activos; se analizan hasta {self.max_cards}"
                      + (" (solo del rubro del cliente)" if focus else ""))
                self._scroll(page, rounds=12 if focus else (2 if self.max_cards <= 10 else 8))
                before = len(out)
                self._take(page, competitor, country, out, assets, keywords=focus)
                got = len(out) - before
                if got < self.max_cards:
                    # Menos de N activos: se completa con los más vistos ya finalizados (siguen siendo datos reales)
                    page.goto(self.url_for(competitor, country, status="all"), wait_until="domcontentloaded", timeout=90_000)
                    page.wait_for_timeout(4000)
                    self._scroll(page, rounds=12 if focus else 3)
                    self._take(page, competitor, country, out, assets, limit=self.max_cards - got, keywords=focus)
                    print(f"[meta_web] {competitor.slug} ({country}): {got} activos + {len(out) - before - got} finalizados")
                if len(out) == before and not focus:
                    # La Biblioteca tiene anuncios pero no se pudo leer ninguna tarjeta: guardar evidencia para corregir
                    dbg = settings.assets_dir / "debug"
                    dbg.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(dbg / f"meta_{competitor.slug}_{country}.png"), full_page=True)
                    (dbg / f"meta_{competitor.slug}_{country}.html").write_text(page.content(), encoding="utf-8")
                    print(f"[meta_web] ATENCIÓN: 0 anuncios leídos de {competitor.slug} ({country}) "
                          f"aunque la Biblioteca declara ~{total if total is not None else '?'}; captura en {dbg}")
            browser.close()
        return out

    def _take(self, page, competitor: CompetitorConfig | None, country: str, out: list[RawCreative], assets: Path, limit: int | None = None,
              market: bool = False, keywords: list[str] | None = None) -> None:
        """Toma hasta `limit` tarjetas nuevas (sin repetir IDs ya tomados) en el orden de la Biblioteca.

        Con `keywords` (rubro del cliente) se saltean las tarjetas que no lo mencionan.
        """
        from ..relevance import matches

        limit = self.max_cards if limit is None else limit
        cards = page.locator("div[class*='x1plvlek'] >> xpath=ancestor::div[contains(@class,'xh8yej3')][1]")
        if cards.count() == 0:
            cards = page.locator("div:has-text('Library ID'), div:has-text('Identificador de la biblioteca')").locator("xpath=ancestor::div[1]")
        taken = 0
        for i in range(cards.count()):
            if taken >= limit:
                break
            card = cards.nth(i)
            try:
                text = card.inner_text(timeout=5000)
            except Exception:
                continue
            ext_id = (ID_RE.search(text) or [None, ""])[1] if ID_RE.search(text) else ""
            if not ext_id or any(r.external_id == ext_id for r in out):
                continue
            if keywords and not matches(text, keywords):
                continue
            taken += 1
            start = START_RE.search(text)
            span = RANGE_RE.search(text)
            page_name = page_name_of(text)
            if market:  # búsqueda por palabra: cada tarjeta es de un anunciante distinto
                from ..discovery import slugify

                competitor = CompetitorConfig(slug="mkt-" + slugify(page_name or "anunciante"), name=page_name or "Anunciante",
                                              country=country, tier="mercado")
            try:  # que la tarjeta esté en pantalla para que las imágenes tengan su tamaño real
                card.scroll_into_view_if_needed(timeout=5000)
                page.wait_for_timeout(300)
            except Exception:
                pass
            img_info = card.locator("img").evaluate_all(
                "els => els.map(e => { const r = e.getBoundingClientRect();"
                " return {src: e.src, w: e.naturalWidth || e.width, h: e.naturalHeight || e.height, rw: r.width, rh: r.height}; })")
            imgs, carousel = pick_images(img_info)
            vids = card.locator("video").evaluate_all("els => els.map(e => e.src || (e.querySelector('source')||{}).src)")
            shot = ""
            if self.screenshots:
                shot_path = assets / f"meta_{ext_id}.png"
                try:
                    self._shoot_creative(page, card, shot_path)
                    shot = str(shot_path)
                except Exception:
                    shot = ""
            parsed = parse_card_text(text, competitor.name)
            first = parse_human_date(start.group(1)) if start else (parse_human_date(span.group(1)) if span else None)
            last = parse_human_date(span.group(2)) if span and not start else None
            out.append(
                RawCreative(
                    competitor_slug=competitor.slug,
                    platform="meta",
                    source="meta_web",
                    external_id=ext_id,
                    format="video" if any(vids) else "carousel" if carousel else "imagen" if any(imgs) else "desconocido",
                    placements=["feed"],
                    headline=parsed["headline"],
                    body_text=parsed["body"],
                    description=parsed["description"],
                    cta_text=parsed["cta"],
                    landing_url=parsed["landing"],
                    media_urls=[u for u in (vids or imgs) if u],
                    thumbnail_url=next((u for u in imgs if u), ""),
                    snapshot_url=f"https://www.facebook.com/ads/library/?id={ext_id}",
                    countries=[] if country.upper() == "ALL" else [country],
                    first_seen=first,
                    last_seen=last,
                    is_active=not INACTIVE_RE.search(text),
                    raw={"card_text": text, "screenshot": shot, "multiple_versions": parsed["multiple_versions"], "page_name": page_name},
                )
            )

    @staticmethod
    def _shoot_creative(page, card, path) -> None:
        """Captura solo la pieza (video o imagen más grande de la tarjeta); si no hay, la tarjeta entera."""
        card.scroll_into_view_if_needed(timeout=5000)
        page.wait_for_timeout(800)  # que termine de cargar la imagen
        target = None
        if card.locator("video").count():
            target = card.locator("video").first
        else:
            areas = card.locator("img").evaluate_all(
                "els => els.map(e => { const r = e.getBoundingClientRect(); return r.width * r.height; })")
            if areas and max(areas) >= 150 * 150:
                target = card.locator("img").nth(areas.index(max(areas)))
        (target or card).screenshot(path=str(path), timeout=10000)

    @staticmethod
    def _scroll(page, rounds: int = 8) -> None:
        for _ in range(rounds):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1500)


UI_NOISE = re.compile(
    r"^(Library ID|Identificador de la biblioteca|ID de la biblioteca|Started running|Comenzó a publicarse|En circulación"
    r"|Activo$|Active$|Inactivo$|Inactive$|Plataformas|Platforms|Ver detalles|See ad details|Ver resumen|See summary"
    r"|Patrocinado$|Publicidad$|Sponsored$|Abrir menú|Open Drop|Este anuncio tiene varias versiones|This ad has multiple versions"
    r"|Detalles del anuncio|Ad details|Categor[ií]a|Category|Transparencia de la UE|EU transparency"
    r"|Ir al perfil de|Go to .* profile|Ver perfil|View profile|Seguir$|Follow$)",
    re.I,
)
CTA_LABELS = {
    "más información", "mas informacion", "más info", "cotizar", "cotizá", "cotiza", "contratar", "contratá", "comprar", "comprá",
    "registrarte", "registrarse", "descargar", "enviar mensaje", "obtener oferta", "reservar", "solicitar", "ver más", "ver mas",
    "learn more", "sign up", "get quote", "get offer", "shop now", "contact us", "apply now", "download", "book now", "send message",
    "whatsapp", "enviar mensaje de whatsapp", "llamar ahora", "call now", "suscribirte", "subscribe",
}
DOMAIN_RE = re.compile(r"^(https?://)?[a-z0-9.-]+\.[a-z]{2,}(/\S*)?$", re.I)
# "2 anuncios usan este contenido y texto" (aviso de la Biblioteca, no es copy)
SHARED_CREATIVE_RE = re.compile(r"^\d+\s+(anuncios? usan? este contenido|ads? uses? this creative)", re.I)
# Categoría de la página que la Biblioteca muestra junto al nombre: "Furniture, Home Design, Business"
CATEGORY_LINE_RE = re.compile(r"^[A-ZÁÉÍÓÚ][\w&'/-]*( [A-Za-zÁÉÍÓÚáéíóú&'/-]+){0,3}(, [A-ZÁÉÍÓÚ][\w&'/-]*( [A-Za-zÁÉÍÓÚáéíóú&'/-]+){0,3}){1,4}$")
TIMESTAMP_RE = re.compile(r"^\s*\d{1,2}:\d{2}(\s*/\s*\d{1,2}:\d{2})?\s*$")


RESULTS_RE = re.compile(r"~?\s*([\d][\d.,]*)\s*(mil\s+)?(resultados?|results?)\b", re.I)


def pick_images(img_info: list[dict]) -> tuple[list[str], bool]:
    """Imágenes de la pieza en el orden en que se VEN (la primera es la que acompaña al titular).

    Se mide el tamaño en pantalla, no el del archivo: en los anuncios de catálogo y carrusel la Biblioteca carga
    también las otras placas, y elegir por tamaño de archivo mezclaba la foto de un producto con el texto de otro.
    Devuelve (urls, es_carrusel).
    """
    shown = [i for i in img_info if i.get("src") and (i.get("rw") or 0) >= 120 and (i.get("rh") or 0) >= 120]
    if shown:
        top = max(i["rw"] * i["rh"] for i in shown)
        main = [i for i in shown if i["rw"] * i["rh"] >= 0.6 * top]  # las placas grandes, en orden de aparición
        rest = [i for i in shown if i not in main]
        return [i["src"] for i in main + rest], len(main) >= 2
    big = [i for i in img_info if i.get("src") and i.get("w", 0) >= 150 and i.get("h", 0) >= 150]
    return [i["src"] for i in big], False


def declared_results(text: str) -> int | None:
    """Cantidad de anuncios que declara la Biblioteca ("~1.300 resultados", "~1,300 results", "1 resultado")."""
    m = RESULTS_RE.search(text or "")
    if not m:
        return None
    n = int(re.sub(r"[.,]", "", m.group(1)) or 0)
    return n * 1000 if m.group(2) else n


def page_name_of(card_text: str) -> str:
    """Nombre del anunciante: la línea que sigue a "Ver detalles del anuncio" en la tarjeta."""
    lines = [l.strip() for l in card_text.splitlines() if l.strip()]
    for i, l in enumerate(lines):
        if re.match(r"(Ver detalles|See ad details|Ver resumen|See summary)", l, re.I) and i + 1 < len(lines):
            return lines[i + 1][:120]
    return ""


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
        if UI_NOISE.search(l) or TIMESTAMP_RE.match(l) or SHARED_CREATIVE_RE.match(l):
            continue
        if not lines and CATEGORY_LINE_RE.match(l):
            continue  # categoría de la página, antes del texto del anuncio
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
            landing = re.sub(r"^https?://", "", l.lower())
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
