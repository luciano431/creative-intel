"""Google Ads Transparency Center vía navegador (Playwright). Best-effort, sin costo.

Dos modos:
- por anunciante: https://adstransparency.google.com/advertiser/{AR...}?region={XX}
- por dominio (cuando no hay ID): https://adstransparency.google.com/?region={XX}&domain={sitio}
  El dominio sale de `google_domain` o, si no está, del `website` del competidor.
Recorre la grilla, abre cada creativo y extrae el contenido desde el componente
`creative-details` > `creative-preview` (shadow DOM + iframes propios del creativo),
ignorando la grilla de "más anuncios de este anunciante" que aparece debajo.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from ..config import CompetitorConfig, settings
from .base import RawCreative


DEEP_DUMP_JS = """() => {
  const skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
  // Busca un selector atravesando shadow roots (incluido el del propio root)
  const find = (root, sel) => {
    const hit = root.querySelector(sel); if (hit) return hit;
    if (root.shadowRoot) { const h = find(root.shadowRoot, sel); if (h) return h; }
    for (const el of root.querySelectorAll('*')) { if (el.shadowRoot) { const h = find(el.shadowRoot, sel); if (h) return h; } }
    return null;
  };
  const dump = (root, label) => {
    const out = {texts: [], images: [], iframes: 0, tags: {}};
    const walk = (r) => {
      for (const el of r.querySelectorAll('*')) {
        const t = el.tagName.toLowerCase();
        if (t.includes('-')) out.tags[t] = (out.tags[t] || 0) + 1;
        if (t === 'img') out.images.push({src: el.currentSrc || el.src, w: el.naturalWidth, h: el.naturalHeight, alt: el.alt || ''});
        if (t === 'iframe') { out.iframes++; el.setAttribute('data-ci-frame', label); }
        if (el.shadowRoot) walk(el.shadowRoot);
      }
      const tw = document.createTreeWalker(r, NodeFilter.SHOW_TEXT);
      let n; while ((n = tw.nextNode())) {
        if (n.parentElement && skip.has(n.parentElement.tagName)) continue;
        const s = n.textContent.trim(); if (s) out.texts.push(s);
      }
      if (r.shadowRoot) walk(r.shadowRoot);
    };
    walk(root);
    return out;
  };
  const findAll = (root, sel, acc) => {
    acc.push(...root.querySelectorAll(sel));
    if (root.shadowRoot) findAll(root.shadowRoot, sel, acc);
    for (const el of root.querySelectorAll('*')) { if (el.shadowRoot) findAll(el.shadowRoot, sel, acc); }
    return acc;
  };
  const details = find(document, 'creative-details');
  // Candidatos a contener el creativo principal; se elige el primero con contenido real
  const cands = details ? findAll(details, 'creative-preview, html-renderer, fletch-renderer, video-renderer, youtube-renderer', []) : [];
  const summary = cands.map((el) => {
    const d = dump(el, 'cand');
    const score = d.images.filter(i => i.w > 0).length * 2 + d.iframes * 3 + (d.texts.join('').length > 0 ? 1 : 0);
    return {tag: el.tagName.toLowerCase(), score, texts: d.texts.length, images: d.images.length, iframes: d.iframes};
  });
  let idx = summary.findIndex(s => s.score > 0); if (idx < 0 && cands.length) idx = 0;
  const preview = idx >= 0 ? cands[idx] : null;
  const d = dump(details || document, 'details');
  const c = preview ? dump(preview, 'creative') : null;
  const html = preview ? ((preview.shadowRoot ? '<!-- shadow -->' + preview.shadowRoot.innerHTML : '') + preview.innerHTML).slice(0, 6000) : '';
  // Aviso de "anuncio quitado" solo si está VISIBLE (el explorador de políticas trae el mismo texto oculto)
  const removedRe = /no podemos mostrar este anuncio|Se quit[oó] debido|can't show this ad/i;
  const visible = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  let removedVisible = false;
  if (details) {
    for (const el of findAll(details, '*', [])) {
      if (el.children.length === 0 && removedRe.test(el.textContent || '') && visible(el)) { removedVisible = true; break; }
    }
  }
  return {details_scoped: !!details, creative_scoped: !!preview, details: d, creative: c, candidates: summary, html, removed_visible: removedVisible};
}"""
READY_JS = """() => {
  const find = (root, sel, acc) => {
    acc.push(...root.querySelectorAll(sel));
    if (root.shadowRoot) find(root.shadowRoot, sel, acc);
    for (const el of root.querySelectorAll('*')) { if (el.shadowRoot) find(el.shadowRoot, sel, acc); }
    return acc;
  };
  const first = (root, sel) => { const a = find(root, sel, []); return a.length ? a[0] : null; };
  const details = first(document, 'creative-details');
  if (!details) return false;
  const removedRe = /no podemos mostrar este anuncio|Se quit[oó] debido|can't show this ad/i;
  for (const el of find(details, '*', [])) {
    if (el.children.length === 0 && removedRe.test(el.textContent || '')) {
      const r = el.getBoundingClientRect(); if (r.width > 0 && r.height > 0) return true;
    }
  }
  for (const r of find(details, 'creative-preview, html-renderer, fletch-renderer, video-renderer, youtube-renderer', [])) {
    const roots = [r]; if (r.shadowRoot) roots.push(r.shadowRoot);
    for (const root of roots) {
      if (root.querySelector('iframe')) return true;
      if (Array.from(root.querySelectorAll('img')).some(i => i.naturalWidth > 50)) return true;
      if ((root.textContent || '').trim().length > 20) return true;
    }
  }
  return false;
}"""
FRAME_DUMP_JS = """() => ({
  text: document.body ? document.body.innerText : '',
  images: Array.from(document.images).map(i => ({src: i.currentSrc || i.src, w: i.naturalWidth, h: i.naturalHeight, alt: i.alt || ''})),
})"""
REMOVED_RE = re.compile(r"no podemos mostrar este anuncio|Se quit[oó] debido|can't show this ad|removed (due to|for) (a )?polic", re.I)
UI_STRINGS = {"close", "flag", "chevron_left", "chevron_right", "arrow_forward", "keyboard_arrow_right", "hide_image", "Acceder",
              "Detalles del anuncio", "Denunciar este anuncio", "Preguntas frecuentes", "Página principal", "Centro de transparencia de anuncios",
              "Privacidad", "Condiciones", "Políticas de Ads", "Principios", "Blog de Ads", "Privacy", "Terms", "Ads Policies",
              "Nuestras políticas de anuncios", "Texto", "Imagen", "Video", "Anuncio", "Ad", "Patrocinado", "Sponsored", "Abrir"}
CHROME_PREFIXES = ("Última vez que se mostró", "Last shown", "Formato:", "Format:", "Se mostraron en", "Shown in", "Denunciar este anuncio",
                   "Ver más anuncios", "See more ads", "La información acerca de este anuncio", "Information about this ad")
NETWORK_ERR_RE = re.compile(r"ERR_INTERNET_DISCONNECTED|ERR_NETWORK_CHANGED|ERR_NAME_NOT_RESOLVED|ERR_CONNECTION_(RESET|CLOSED|REFUSED|TIMED_OUT)|ERR_TIMED_OUT|Timeout \d+ms exceeded")
YT_ID_RE = re.compile(r"youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=)([\w-]{11})")
DATE_LINE_RE = re.compile(r"^\d{1,2}\s+\w{3,10}\.?\s+\d{4}$|^\w{3,10}\.?\s+\d{1,2},?\s+\d{4}$")
DOMAIN_LINE_RE = re.compile(r"^(https?://)?[a-z0-9.-]+\.[a-z]{2,}(/\S*)?$", re.I)


def domain_of(competitor: CompetitorConfig, country: str = "") -> str:
    """Dominio a buscar: el de ese país (google_domain como mapa), el google_domain único o el website."""
    per_country = getattr(competitor, "google_domains", {}) or {}
    raw = per_country.get(country.upper()) or getattr(competitor, "google_domain", "") or competitor.website or ""
    if not raw and per_country:
        raw = next(iter(per_country.values()))
    if not raw:
        return ""
    host = urlparse(raw if "://" in raw else "https://" + raw).netloc or raw
    return host.lower().removeprefix("www.")


def region_param(country: str) -> str:
    return "anywhere" if country.upper() == "ALL" else country.upper()


def clean_lines(texts: list[str], advertiser: str, extra_drop: tuple[str, ...] = ()) -> list[str]:
    """Filtra el cromo de la página y deja solo el texto del anuncio."""
    adv_tokens = {t for t in re.split(r"\W+", advertiser.lower()) if len(t) > 3}
    out: list[str] = []
    for t in texts:
        if t in UI_STRINGS or t in extra_drop or any(t.startswith(p) for p in CHROME_PREFIXES):
            continue
        if DATE_LINE_RE.match(t) or re.match(r"^\d+ de \d+ variaci|^\d+ of \d+ variation", t) or REMOVED_RE.search(t):
            continue
        if t.lower() == advertiser.lower() or (t.isupper() and adv_tokens & set(re.split(r"\W+", t.lower()))):
            continue  # nombre legal del anunciante en el encabezado
        if not re.search(r"\w", t):
            continue
        out.append(t)
    return out


class GoogleTransparencyWebCollector:
    name = "google_web"
    platform = "google"

    def __init__(self, headless: bool = True, max_cards: int | None = None):
        self.headless = headless
        # Tope de anuncios por competidor y país. Cada anuncio se abre uno por uno (~5 s c/u),
        # así que 40 por competidor alcanza para el plan y la corrida tarda minutos, no horas.
        self.max_cards = max_cards or int(os.getenv("GOOGLE_MAX_CARDS", "40"))

    def supports(self, competitor: CompetitorConfig) -> bool:
        return bool(competitor.google_advertiser_id or domain_of(competitor))

    def url_for(self, competitor: CompetitorConfig, country: str) -> str:
        region = region_param(country)
        if competitor.google_advertiser_id:
            return f"https://adstransparency.google.com/advertiser/{competitor.google_advertiser_id}?region={region}"
        return f"https://adstransparency.google.com/?region={region}&domain={domain_of(competitor, country)}"

    def collect(self, competitor: CompetitorConfig) -> list[RawCreative]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Instalar playwright: pip install playwright && playwright install chromium") from exc

        out: list[RawCreative] = []
        failed = 0
        shots = settings.assets_dir / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        debug = bool(os.getenv("CREATIVE_INTEL_DEBUG"))
        dbg = settings.assets_dir / "debug"
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless, executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_PATH") or None)
            page = browser.new_page(viewport={"width": 1400, "height": 1000}, locale="es-UY")
            captured: list[tuple[str, str]] = []

            def on_response(resp):
                url = resp.url
                if "adstransparency.google.com" not in url or "SearchCreatives" not in url and "SearchService" not in url:
                    return
                try:
                    body = resp.text()
                except Exception:
                    return
                for pair in ids_from_rpc(body):
                    if pair not in captured:
                        captured.append(pair)

            page.on("response", on_response)
            for country in competitor.ad_countries:
                captured.clear()
                page.goto(self.url_for(competitor, country), wait_until="domcontentloaded", timeout=90_000)
                page.wait_for_timeout(5000)
                hrefs = self._load_grid(page, captured)
                # Links armados desde las respuestas de la API interna (cubren tarjetas sin <a>)
                region = region_param(country)
                for adv, cr in captured:
                    if not any(cr in h for h in hrefs):
                        hrefs.append(f"/advertiser/{adv}/creative/{cr}?region={region}")
                hrefs = hrefs[: self.max_cards]
                if debug:
                    self._grid_debug(page, dbg, competitor.slug, country, hrefs, captured)
                total = _declared_total(page.inner_text("body"))
                print(f"[google_web] {competitor.slug} ({country}): {len(hrefs)} tarjetas capturadas"
                      + (f" de ~{total} que declara Google" if total is not None else ""))
                if not hrefs:
                    dbg.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(dbg / f"google_{competitor.slug}_{country}.png"), full_page=True)
                    (dbg / f"google_{competitor.slug}_{country}.html").write_text(page.content(), encoding="utf-8")
                    print(f"[google_web] 0 tarjetas para {competitor.slug} ({country}); diagnóstico guardado en {dbg}")
                    continue
                for href in hrefs:
                    m = re.search(r"/creative/(CR\w+)", href)
                    if not m:
                        continue
                    ext_id = m.group(1)
                    url = ("https://adstransparency.google.com" + href) if href.startswith("/") else href
                    detail = None
                    for attempt, pause in enumerate((0, 15, 45, 120)):
                        if pause:
                            print(f"[google_web] red caída, reintento {attempt}/3 de {ext_id} en {pause}s")
                            page.wait_for_timeout(pause * 1000)
                        try:
                            detail = self._detail(page, url, ext_id, shots, debug, dbg, competitor)
                            break
                        except Exception as exc:
                            msg = str(exc).splitlines()[0]
                            if not NETWORK_ERR_RE.search(msg):
                                print(f"[google_web] detalle {ext_id} falló: {msg}")
                                break
                    if detail is None:
                        failed += 1
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
                            thumbnail_url=next((u for u in detail["media"] if "youtube.com/watch" not in u), ""),
                            snapshot_url=url,
                            countries=[] if country.upper() == "ALL" else [country],
                            first_seen=detail["first_seen"],
                            last_seen=detail["last_seen"],
                            is_active=True,
                            raw={"page_text": detail["page_text"][:3000], "screenshot": detail["screenshot"], "frame_texts": detail["frame_texts"][:5],
                                 "variations": detail["variations"], "regions": detail["regions"], "removed": detail["removed"],
                                 "scoped": detail["scoped"], "text_in_image": detail["text_in_image"]},
                        )
                    )
            browser.close()
        if failed:
            print(f"[google_web] {competitor.slug}: {failed} creativos no se pudieron abrir (quedan para la próxima corrida)")
        return out

    def _grid_debug(self, page, dbg, slug: str, country: str, hrefs: list[str], captured) -> None:
        dbg.mkdir(parents=True, exist_ok=True)
        info = page.evaluate("""() => {
          const all = (root, acc) => { for (const el of root.querySelectorAll('*')) { acc.push(el); if (el.shadowRoot) all(el.shadowRoot, acc); } return acc; };
          const els = all(document, []);
          const tags = {}; els.forEach(e => { const t = e.tagName.toLowerCase(); if (t.includes('-')) tags[t] = (tags[t] || 0) + 1; });
          const buttons = els.filter(e => e.tagName === 'BUTTON' || e.tagName === 'MATERIAL-BUTTON' || e.getAttribute('role') === 'button')
                             .map(e => (e.innerText || e.getAttribute('aria-label') || '').trim()).filter(Boolean).slice(0, 60);
          const scrollers = els.filter(e => e.scrollHeight > e.clientHeight + 50 && /auto|scroll/.test(getComputedStyle(e).overflowY))
                               .map(e => e.tagName.toLowerCase() + ' ' + e.scrollTop + '/' + e.scrollHeight);
          return {tags, buttons, scrollers, anchors: document.querySelectorAll("a[href*='/creative/']").length, docHeight: document.documentElement.scrollHeight};
        }""")
        page.screenshot(path=str(dbg / f"google_grid_{slug}_{country}.png"), full_page=True)
        (dbg / f"google_grid_{slug}_{country}.txt").write_text(
            f"anchors: {info['anchors']} | hrefs finales: {len(hrefs)} | ids por API: {len(captured)} | alto doc: {info['docHeight']}\n"
            + "\n===== BOTONES =====\n" + "\n".join(info["buttons"])
            + "\n\n===== CONTENEDORES CON SCROLL =====\n" + "\n".join(info["scrollers"])
            + "\n\n===== ELEMENTOS CUSTOM =====\n" + ", ".join(f"{k}:{v}" for k, v in sorted(info["tags"].items())), encoding="utf-8")

    def _load_grid(self, page, captured=None) -> list[str]:
        """Carga la grilla completa: scroll sobre la grilla (no la esquina de la ventana),
        scrollIntoView de la última tarjeta y clic en "Ver más" si aparece. Corta cuando
        ni los links del DOM ni los IDs capturados de la API crecen, o al llegar a max_cards."""
        captured = captured if captured is not None else []

        def current() -> list[str]:
            out: list[str] = []
            for h in page.locator("a[href*='/creative/']").evaluate_all("els => els.map(e => e.getAttribute('href'))"):
                if h and h not in out:
                    out.append(h)
            return out

        hrefs = current()
        size = len(hrefs) + len(captured)
        stale = 0
        for _ in range(60):
            if size >= self.max_cards:
                break
            try:
                page.mouse.move(700, 600)
                last = page.locator("a[href*='/creative/']").last
                if last.count():
                    last.scroll_into_view_if_needed(timeout=3000)
                page.mouse.wheel(0, 3000)
                page.evaluate("() => { window.scrollBy(0, 3000); document.querySelectorAll('*').forEach(e => { if (e.scrollHeight > e.clientHeight + 50 && /auto|scroll/.test(getComputedStyle(e).overflowY)) e.scrollTop = e.scrollHeight; }); }")
                more = page.get_by_role("button", name=re.compile(r"ver m[aá]s|mostrar m[aá]s|ver todos|see more|show more|see all|load more", re.I))
                if more.count() and more.first.is_visible():
                    more.first.click(timeout=3000)
            except Exception:
                pass
            page.wait_for_timeout(1500)
            hrefs = current()
            new_size = len(hrefs) + len(captured)
            stale = stale + 1 if new_size <= size else 0
            size = new_size
            if stale >= 3:
                break
        return hrefs

    def _detail(self, page, url: str, ext_id: str, shots, debug: bool, dbg, competitor: CompetitorConfig) -> dict:
        """Abre la página de detalle de un creativo y extrae contenido, fechas y captura."""
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        # El creativo se renderiza después del primer paint: esperar hasta que haya contenido
        # (imagen cargada, iframe, texto) o el aviso de anuncio quitado, con tope de 15 s
        for _ in range(15):
            page.wait_for_timeout(1000)
            try:
                if page.evaluate(READY_JS):
                    break
            except Exception:
                pass
        page.wait_for_timeout(2000)
        page_text = page.inner_text("body")
        m_fmt = re.search(r"Formato:\s*(\w+)|Format:\s*(\w+)", page_text, re.I)
        fmt_label = ((m_fmt.group(1) or m_fmt.group(2)) if m_fmt else "").lower()
        fmt = {"texto": "texto", "text": "texto", "imagen": "imagen", "image": "imagen", "video": "video", "vídeo": "video"}.get(fmt_label, "desconocido")
        m_var = re.search(r"de\s+(\d+)\s+variaci|of\s+(\d+)\s+variation", page_text, re.I)
        variations = int(m_var.group(1) or m_var.group(2)) if m_var else 1
        m_reg = re.search(r"Se mostraron en\s+([^\n]+)|Shown in\s+([^\n]+)", page_text)
        regions = (m_reg.group(1) or m_reg.group(2)).strip() if m_reg else ""

        deep = page.evaluate(DEEP_DUMP_JS)
        scope = deep["creative"] or deep["details"]
        scoped = "creative" if deep["creative_scoped"] else "details" if deep["details_scoped"] else "none"
        # "Quitado": el aviso de política aparece justo debajo de "Formato: ..." para el creativo principal;
        # la grilla de otros anuncios (que también puede tener quitados) queda más abajo y no cuenta.
        head = page_text[m_fmt.end(): m_fmt.end() + 300] if m_fmt else page_text[:600]
        removed = bool(deep.get("removed_visible")) or bool(REMOVED_RE.search(head))

        # Iframes propios del creativo (srcdoc / displayads) marcados por el JS
        frame_texts: list[str] = []
        frame_urls: list[str] = []
        media: list[str] = []
        frame_images: list[dict] = []
        label = "creative" if deep["creative_scoped"] else "details"
        for handle in page.locator(f"iframe[data-ci-frame='{label}']").element_handles():
            fr = handle.content_frame()
            if fr is None:
                continue
            stack = [fr]
            while stack:
                f = stack.pop()
                stack.extend(f.child_frames)
                furl = f.url or ""
                frame_urls.append(furl)
                if "youtube" in furl:
                    media.append(furl)
                    continue
                if "feedback" in furl or "clients6.google.com" in furl:
                    continue
                try:
                    fd = f.evaluate(FRAME_DUMP_JS)
                except Exception:
                    continue
                if fd["text"].strip():
                    frame_texts.append(fd["text"].strip())
                frame_images.extend(fd["images"])
        for im in scope["images"] + frame_images:
            src = im.get("src") or ""
            if src and im["w"] >= 100 and im["h"] >= 60 and "gstatic" not in src and "google.com/images" not in src and "googleusercontent.com/a/" not in src:
                media.append(src)
        # En el archivo de Google los anuncios de texto se guardan como imagen renderizada (simgad):
        # no hay texto en el DOM, se conserva la imagen y el texto lo lee la IA al clasificar.
        yt_ids = list(dict.fromkeys(m.group(1) for u in media for m in [YT_ID_RE.search(u)] if m))
        media = [u for u in media if "youtube" not in u and "googlesyndication.com/pagead/gadgets" not in u]
        if yt_ids:
            media = [f"https://www.youtube.com/watch?v={v}" for v in yt_ids] + [f"https://i.ytimg.com/vi/{v}/hqdefault.jpg" for v in yt_ids] + media

        advertiser = competitor.name
        shadow_texts = clean_lines(scope["texts"], advertiser, extra_drop=(regions, fmt_label.capitalize()))
        text_lines = clean_lines([l.strip() for t in frame_texts for l in t.splitlines() if l.strip()], advertiser) + shadow_texts
        text_lines = list(dict.fromkeys(text_lines))
        if fmt == "desconocido":
            fmt = "video" if any("youtube" in u for u in media) else "imagen" if media else "texto" if text_lines else "desconocido"
        landing = ""
        for l in text_lines:
            if DOMAIN_LINE_RE.match(l) and " " not in l:
                landing = re.sub(r"^https?://", "", l.lower())
                break
        content = [l for l in text_lines if not (DOMAIN_LINE_RE.match(l) and " " not in l)]
        if removed:
            content = []
        headline = content[0] if content else ""
        body = "\n".join(content[1:])[:4000] if len(content) > 1 else ""
        if removed:
            body = "(Anuncio quitado por Google por incumplimiento de política: contenido no disponible)"
        shot = shots / f"google_{ext_id}.png"
        try:
            page.screenshot(path=str(shot), full_page=False)
        except Exception:
            shot = None
        if debug:
            dbg.mkdir(parents=True, exist_ok=True)
            (dbg / f"google_detail_{competitor.slug}_{ext_id}.txt").write_text(
                page_text
                + f"\n\n===== SCOPE: {scoped} | REMOVED: {removed} | FORMATO: {fmt} =====\n"
                + "\n===== TEXTOS DEL CREATIVO (shadow DOM, filtrados) =====\n" + "\n".join(shadow_texts)
                + "\n\n===== TEXTOS DEL CREATIVO (crudos) =====\n" + "\n".join(scope["texts"][:80])
                + "\n\n===== IFRAMES DEL CREATIVO =====\n" + "\n".join(frame_urls)
                + "\n\n===== TEXTOS EN IFRAMES =====\n" + "\n---\n".join(frame_texts)
                + "\n\n===== IMAGENES (src w x h alt) =====\n" + "\n".join(f"{i['src'][:140]} {i['w']}x{i['h']} {i['alt'][:40]}" for i in scope["images"] + frame_images)
                + "\n\n===== ELEMENTOS CUSTOM (details) =====\n" + ", ".join(f"{k}:{v}" for k, v in sorted(deep["details"]["tags"].items()))
                + "\n\n===== CANDIDATOS (tag score textos imgs iframes) =====\n" + "\n".join(f"{c['tag']} {c['score']} {c['texts']} {c['images']} {c['iframes']}" for c in deep.get("candidates", []))
                + "\n\n===== HTML DEL CREATIVO ELEGIDO (recortado) =====\n" + deep.get("html", "")
                + "\n\n===== RESULTADO =====\n" + f"titular: {headline!r}\nlanding: {landing!r}\nbody: {body[:300]!r}\nmedia: {media}\n", encoding="utf-8")
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
            "variations": variations,
            "regions": regions,
            "removed": removed,
            "scoped": scoped,
            "text_in_image": bool(not content and not removed and any("youtube.com/watch" not in u for u in media)),
        }


def ids_from_rpc(body: str) -> list[tuple[str, str]]:
    """Pares (anunciante AR..., creativo CR...) en el orden en que aparecen en una respuesta de la API."""
    pairs: list[tuple[str, str]] = []
    adv = ""
    for m in re.finditer(r"\b(AR\d{10,})\b|\b(CR\d{10,})\b", body):
        if m.group(1):
            adv = m.group(1)
        elif adv and (adv, m.group(2)) not in pairs:
            pairs.append((adv, m.group(2)))
    return pairs


def _declared_total(text: str) -> int | None:
    """Cantidad de anuncios que declara la página ("~1.234 anuncios", "12 ads")."""
    m = re.search(r"~?\s*([\d.,]+)\s*(mil\s+)?(anuncios|ads)\b", text, re.I)
    if not m:
        return None
    n = int(re.sub(r"[.,]", "", m.group(1)) or 0)
    return n * 1000 if m.group(2) else n


def _date_after(text: str, keyword_re: str):
    from .meta_web import parse_human_date

    m = re.search(keyword_re + r"[^\n]{0,40}?(\d{1,2}\s+\S+\.?\s+\d{4}|\w{3,10}\.? \d{1,2},? \d{4})", text, re.I)
    return parse_human_date(m.group(2)) if m else None
