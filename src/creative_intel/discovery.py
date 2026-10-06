"""Búsqueda de IDs para dar de alta clientes y competidores.

- Meta: busca en la Biblioteca de anuncios por nombre (o por palabras del rubro) y lee de las
  respuestas de la página el ID de cada anunciante, su nombre, cuántos anuncios vimos y a qué
  dominios llevan esos anuncios.
- Google: con un dominio, abre Ads Transparency y cuenta cuántos anuncios declara y qué IDs de
  anunciante (AR...) aparecen.

Las funciones de lectura (`pages_from_text`, `rank_pages`) son puras para poder testearlas sin red.
"""
from __future__ import annotations

import bisect
import json
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable
from urllib.parse import quote_plus, urlparse

PAGE_ID_RE = re.compile(r'"page_id"\s*:\s*"?(\d{5,})"?')
PAGE_NAME_RE = re.compile(r'"page_name"\s*:\s*"((?:[^"\\]|\\.)*)"')
LIKES_RE = re.compile(r'"page_like_count"\s*:\s*(\d+)')
AD_ID_RE = re.compile(r'"ad_archive_id"\s*:\s*"?(\d+)"?')
CAPTION_RE = re.compile(r'"caption"\s*:\s*"((?:[^"\\]|\\.)*)"')
LINK_RE = re.compile(r'"link_url"\s*:\s*"((?:[^"\\]|\\.)*)"')
NOT_ADVERTISER_DOMAINS = (
    "facebook.com", "fb.me", "fb.com", "instagram.com", "wa.me", "whatsapp.com", "m.me", "messenger.com",
    "bit.ly", "linktr.ee", "apps.apple.com", "play.google.com", "youtube.com", "goo.gl", "tinyurl.com",
)


@dataclass
class MetaPage:
    page_id: str
    name: str = ""
    ads: set[str] = field(default_factory=set)
    likes: int | None = None
    domains: Counter = field(default_factory=Counter)
    score: float = 0.0

    @property
    def top_domain(self) -> str:
        return self.domains.most_common(1)[0][0] if self.domains else ""

    def as_dict(self) -> dict[str, Any]:
        return {"page_id": self.page_id, "name": self.name, "ads": len(self.ads), "likes": self.likes,
                "domains": [d for d, _ in self.domains.most_common(3)], "score": round(self.score, 2)}


def _unescape(s: str) -> str:
    try:
        return json.loads(f'"{s}"')
    except Exception:
        return s


def _nearest(positions: list[int], values: list[Any], pos: int, window: int) -> Any:
    """El valor cuya posición en el texto está más cerca de `pos` (dentro de `window` caracteres)."""
    if not positions:
        return None
    i = bisect.bisect_left(positions, pos)
    best, dist = None, window + 1
    for j in (i - 1, i):
        if 0 <= j < len(positions) and abs(positions[j] - pos) < dist:
            best, dist = values[j], abs(positions[j] - pos)
    return best


def domain_of_url(url: str) -> str:
    url = (url or "").strip().lower()
    if not url:
        return ""
    host = urlparse(url if "://" in url else "https://" + url).netloc or url.split("/")[0]
    host = host.split("@")[-1].split(":")[0].removeprefix("www.").removeprefix("m.")
    if "." not in host or " " in host or any(host == d or host.endswith("." + d) for d in NOT_ADVERTISER_DOMAINS):
        return ""
    return host


def pages_from_text(text: str) -> dict[str, MetaPage]:
    """Anunciantes que aparecen en las respuestas de la Biblioteca de anuncios de Meta.

    No dependemos del orden de las claves en el JSON: cada dato (nombre, anuncio, dominio, me gusta)
    se asigna al `page_id` más cercano en el texto.
    """
    pid_m = list(PAGE_ID_RE.finditer(text))
    if not pid_m:
        return {}
    pid_pos = [m.start() for m in pid_m]
    pid_val = [m.group(1) for m in pid_m]
    pages: dict[str, MetaPage] = {pid: MetaPage(pid) for pid in dict.fromkeys(pid_val)}

    names: dict[str, Counter] = {pid: Counter() for pid in pages}
    for m in PAGE_NAME_RE.finditer(text):
        pid = _nearest(pid_pos, pid_val, m.start(), 1500)
        if pid:
            names[pid][_unescape(m.group(1)).strip()] += 1
    for m in AD_ID_RE.finditer(text):
        pid = _nearest(pid_pos, pid_val, m.start(), 6000)
        if pid:
            pages[pid].ads.add(m.group(1))
    for rx in (CAPTION_RE, LINK_RE):
        for m in rx.finditer(text):
            dom = domain_of_url(_unescape(m.group(1)))
            pid = _nearest(pid_pos, pid_val, m.start(), 6000) if dom else None
            if pid:
                pages[pid].domains[dom] += 1
    for m in LIKES_RE.finditer(text):
        pid = _nearest(pid_pos, pid_val, m.start(), 6000)
        if pid:
            pages[pid].likes = max(pages[pid].likes or 0, int(m.group(1)))
    for pid, c in names.items():
        if c:
            pages[pid].name = c.most_common(1)[0][0]
    return pages


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


GENERIC_NAME_WORDS = {
    "uruguay", "uy", "oficial", "official", "store", "tienda", "online", "shop", "sa", "srl", "s", "r", "l", "ltda",
    "inc", "grupo", "group", "homecenter", "home", "center", "argentina", "ar", "es", "mx", "the", "de", "del", "la", "el",
}


def name_match(query: str, name: str) -> float:
    """0 a 1: qué tanto se parece el nombre de la página al buscado (sin tildes ni mayúsculas)."""
    q, n = _norm(query), _norm(name)
    if not q or not n:
        return 0.0
    foreign_chars = any(ch.isalpha() and not ch.isascii() and unicodedata.category(ch) != "Mn"
                        for ch in unicodedata.normalize("NFKD", name or ""))
    if (q == n or q.replace(" ", "") == n.replace(" ", "")) and not foreign_chars:
        return 1.0
    qt, nt = set(q.split()), set(n.split())
    overlap = len(qt & nt) / len(qt) if qt else 0.0
    # Palabras de más en la página que no son genéricas ("Barraca" en "Barraca Nuevo Cerro" buscando
    # "Cerro Nuevo") indican otro negocio que comparte palabras: no alcanza para darlo por seguro.
    extra = nt - qt - GENERIC_NAME_WORDS
    # Letras de otros alfabetos (coreano, chino, cirílico…) se pierden al normalizar: también cuentan como "de más".
    foreign = sum(1 for ch in unicodedata.normalize("NFKD", name or "")
                  if ch.isalpha() and not ch.isascii() and unicodedata.category(ch) != "Mn")
    if foreign >= 2:
        return min(0.6, max(overlap * 0.6, 0.0))
    score = overlap * (0.9 if not extra else 0.6)
    return max(score, SequenceMatcher(None, q, n).ratio())


def rank_pages(pages: dict[str, MetaPage], query: str = "", exclude: Iterable[str] = ()) -> list[MetaPage]:
    """Por parecido con el nombre buscado (si hay) y por cantidad de anuncios vistos."""
    skip = set(exclude)
    out = []
    for p in pages.values():
        if p.page_id in skip:
            continue
        p.score = name_match(query, p.name) if query else 0.0
        out.append(p)
    return sorted(out, key=lambda p: (-round(p.score, 1), -len(p.ads), -(p.likes or 0)))


def is_confident(p: MetaPage, query: str) -> bool:
    return name_match(query, p.name) >= 0.8 and len(p.ads) > 0


# ---------------------------------------------------------------------------- navegador


def _browser(p, headless: bool):
    return p.chromium.launch(headless=headless, executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_PATH") or None)


def meta_library_url(query: str, country: str, exact: bool = True, active_only: bool = False) -> str:
    return (
        "https://www.facebook.com/ads/library/?active_status=" + ("active" if active_only else "all")
        + f"&ad_type=all&country={country.upper()}&q={quote_plus(query)}"
        + "&search_type=" + ("keyword_exact_phrase" if exact else "keyword_unordered") + "&media_type=all"
    )


def _meta_texts(urls: list[str], scroll_rounds: int = 6, headless: bool = True) -> list[str]:
    """Para cada URL de la Biblioteca devuelve todo el texto de sus respuestas (HTML + API interna)."""
    from playwright.sync_api import sync_playwright

    out: list[str] = []
    with sync_playwright() as p:
        browser = _browser(p, headless)
        page = browser.new_page(viewport={"width": 1400, "height": 1000}, locale="es-UY")
        chunks: list[str] = []

        def on_response(resp):
            if "facebook.com" not in resp.url or not any(k in resp.url for k in ("/api/graphql", "/ads/library")):
                return
            try:
                chunks.append(resp.text())
            except Exception:
                pass

        page.on("response", on_response)
        for url in urls:
            chunks.clear()
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(4000)
            for _ in range(scroll_rounds):
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(1500)
            try:
                chunks.append(page.content())
            except Exception:
                pass
            out.append("\n".join(chunks))
        browser.close()
    return out


def meta_search_text(queries: list[tuple[str, str]], exact: bool = True, scroll_rounds: int = 6, headless: bool = True,
                     active_only: bool = False) -> list[str]:
    """Para cada (consulta, país) devuelve todo el texto de las respuestas de la Biblioteca (HTML + API interna)."""
    return _meta_texts([meta_library_url(q, c, exact, active_only) for q, c in queries], scroll_rounds, headless)


def meta_page_domain(page_id: str, country: str = "ALL", headless: bool = True) -> str:
    """Dominio al que llevan los anuncios de una página (para buscarla en Google)."""
    url = (f"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country={country.upper()}"
           f"&view_all_page_id={page_id}&search_type=page&media_type=all")
    pages = pages_from_text(_meta_texts([url], scroll_rounds=3, headless=headless)[0])
    if page_id in pages and pages[page_id].top_domain:
        return pages[page_id].top_domain
    merged: Counter = Counter()
    for pg in pages.values():
        merged.update(pg.domains)
    return merged.most_common(1)[0][0] if merged else ""


def find_meta_pages(name: str, country: str = "UY", headless: bool = True) -> list[MetaPage]:
    """Candidatos de página de Meta para un nombre, primero los que más se parecen."""
    texts = meta_search_text([(name, country)], exact=True, headless=headless)
    pages = pages_from_text(texts[0])
    if not pages:  # la frase exacta no trajo nada: probar sin comillas
        pages = pages_from_text(meta_search_text([(name, country)], exact=False, headless=headless)[0])
    return rank_pages(pages, query=name)


def discover_advertisers(keywords: list[str], countries: list[str], exclude: Iterable[str] = (), headless: bool = True,
                         scroll_rounds: int = 8) -> list[MetaPage]:
    """Anunciantes que más pautan con esas palabras en esos países (para proponer competidores)."""
    merged: dict[str, MetaPage] = {}
    texts = meta_search_text([(k, c) for c in countries for k in keywords], exact=False, scroll_rounds=scroll_rounds,
                             headless=headless, active_only=True)
    for text in texts:
        for pid, pg in pages_from_text(text).items():
            cur = merged.setdefault(pid, MetaPage(pid))
            cur.name = cur.name or pg.name
            cur.ads |= pg.ads
            cur.domains.update(pg.domains)
            cur.likes = max(cur.likes or 0, pg.likes or 0) or None
    return rank_pages(merged, exclude=exclude)


def google_check(domain: str, region: str = "UY", headless: bool = True) -> dict[str, Any]:
    """Cuántos anuncios declara Google Ads Transparency para un dominio y con qué IDs de anunciante."""
    from playwright.sync_api import sync_playwright

    from .collectors.google_web import _declared_total, ids_from_rpc, region_param

    found: list[tuple[str, str]] = []
    url = f"https://adstransparency.google.com/?region={region_param(region)}&domain={domain}"
    with sync_playwright() as p:
        browser = _browser(p, headless)
        page = browser.new_page(viewport={"width": 1400, "height": 1000}, locale="es-UY")

        def on_response(resp):
            if "adstransparency.google.com" in resp.url and ("SearchCreatives" in resp.url or "SearchService" in resp.url):
                try:
                    found.extend(ids_from_rpc(resp.text()))
                except Exception:
                    pass

        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(6000)
        declared = _declared_total(page.inner_text("body"))
        browser.close()
    advertisers = Counter(a for a, _ in found)
    return {
        "domain": domain,
        "region": region.upper(),
        "declared": declared,
        "creatives_seen": len({c for _, c in found}),
        "advertisers": [a for a, _ in advertisers.most_common(3)],
        "url": url,
    }


# ---------------------------------------------------------------------------- escribir en competitors.yaml


def _entries(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for country in data.get("countries", []) or []:
        if isinstance(country.get("own"), dict):
            yield country["own"]
        for tier in ("local", "global"):
            for item in ((country.get("competitors") or {}).get(tier) or []):
                if isinstance(item, dict):
                    yield item


def update_ids(path: Any, updates: dict[str, dict[str, str]]) -> int:
    """Escribe IDs encontrados en competitors.yaml (por slug). Guarda una copia .bak antes."""
    import shutil
    from pathlib import Path

    import yaml

    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    changed = 0
    for entry in _entries(data):
        for k, v in (updates.get(entry.get("slug")) or {}).items():
            if v and entry.get(k) != v:
                entry[k] = v
                changed += 1
    if changed:
        shutil.copy(path, path.with_suffix(".yaml.bak"))
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    return changed


def slugify(name: str) -> str:
    return re.sub(r"-+", "-", _norm(name).replace(" ", "-")).strip("-")[:40] or "competidor"


def add_competitors(path: Any, pages: list[MetaPage], tier: str, country: str = "") -> list[str]:
    """Agrega anunciantes encontrados como competidores (local o global). Saltea los que ya están."""
    import shutil
    from pathlib import Path

    import yaml

    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    countries = data.get("countries") or []
    if not countries:
        raise ValueError("competitors.yaml no tiene países")
    target = next((c for c in countries if str(c.get("code", "")).upper() == country.upper()), countries[0]) if country else countries[0]
    existing_ids = {str(e.get("meta_page_id") or "") for e in _entries(data)}
    existing_slugs = {e.get("slug") for e in _entries(data)}
    bucket = target.setdefault("competitors", {}).setdefault(tier, [])
    # Saca los ejemplos vacíos de la plantilla (competidor-1, referente-1)
    bucket[:] = [b for b in bucket if b.get("meta_page_id") or b.get("google_domain") or b.get("website")
                 or not re.match(r"^(competidor|referente)-\d+$", str(b.get("slug", "")))]
    added = []
    for pg in pages:
        if pg.page_id in existing_ids:
            continue
        slug = slugify(pg.name)
        while slug in existing_slugs:
            slug += "-2"
        entry = {"slug": slug, "name": pg.name or slug, "website": "", "meta_page_id": pg.page_id,
                 "google_domain": pg.top_domain, "google_advertiser_id": "", "products": [], "active": True}
        if tier == "global":
            entry["ad_countries"] = ["ALL"]
        bucket.append(entry)
        existing_slugs.add(slug)
        added.append(pg.name or slug)
    if added:
        shutil.copy(path, path.with_suffix(".yaml.bak"))
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    return added


def set_fields(path: Any, slug: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Cambia datos de una marca (cliente o competidor) por su slug. Un valor "" borra el dato."""
    import shutil
    from pathlib import Path

    import yaml

    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entry = next((e for e in _entries(data) if e.get("slug") == slug), None)
    if entry is None:
        raise KeyError(slug)
    entry.update(fields)
    shutil.copy(path, path.with_suffix(".yaml.bak"))
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    return entry
