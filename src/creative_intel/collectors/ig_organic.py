"""Publicaciones orgánicas de Instagram de cualquier cuenta profesional pública (vía oficial de Meta).

Usa "Business Discovery" de la Graph API: con el token del Radar (permisos instagram_basic,
pages_show_list y pages_read_engagement) y una cuenta de Instagram profesional propia vinculada a una
página de Facebook, se pueden leer las publicaciones públicas de otra cuenta profesional: texto, fecha,
tipo, likes y cantidad de comentarios. Incluye publicaciones viejas (sirve para ediciones pasadas).
No trae el texto de los comentarios ni las visualizaciones de los reels.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

import httpx

from ..config import settings

POST_FIELDS = "id,caption,like_count,comments_count,timestamp,media_type,media_product_type,media_url,thumbnail_url,permalink"
NOT_HANDLES = {"p", "reel", "reels", "explore", "stories", "accounts", "tv", "sharer", "share", "direct"}
HANDLE_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})/?", re.I)


def find_handle(html: str) -> str:
    """El usuario de Instagram que enlaza una web (encabezado o pie de página)."""
    for m in HANDLE_RE.finditer(html or ""):
        h = m.group(1).strip(".")
        if h.lower() not in NOT_HANDLES:
            return h
    return ""


def handle_from_site(url: str, client: httpx.Client | None = None) -> str:
    if not url:
        return ""
    url = url if "://" in url else "https://" + url
    c = client or httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    try:
        return find_handle(c.get(url).text)
    except Exception:
        return ""


PUBLIC_URL = "https://www.instagram.com/api/v1/users/web_profile_info/"
PUBLIC_HEADERS = {"x-ig-app-id": "936619743392459", "Accept": "*/*", "Accept-Language": "es-UY,es;q=0.9",
                  "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/126.0 Safari/537.36"}


def public_profile(username: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """Perfil público tal como lo ve cualquiera sin iniciar sesión: seguidores y las últimas ~12 publicaciones.

    Sirve cuando Meta no habilita Business Discovery (cuentas de la competencia). No llega a publicaciones viejas:
    para tener la historia de un evento hay que archivar en los días previos y durante (cada corrida suma)."""
    c = client or httpx.Client(timeout=30, follow_redirects=True)
    resp = c.get(PUBLIC_URL, params={"username": username.lstrip("@")}, headers=PUBLIC_HEADERS)
    try:
        user = ((resp.json().get("data") or {}).get("user")) if resp.status_code < 400 else None
    except ValueError:
        user = None
    if not user:
        raise RuntimeError(f"Instagram público: no se pudo leer @{username} (respuesta {resp.status_code})")
    posts = []
    for e in (user.get("edge_owner_to_timeline_media") or {}).get("edges") or []:
        n = e.get("node") or {}
        cap = ((n.get("edge_media_to_caption") or {}).get("edges") or [{}])[0].get("node", {}).get("text", "")
        kind = {"GraphSidecar": "CAROUSEL_ALBUM", "GraphVideo": "VIDEO"}.get(n.get("__typename"), "IMAGE")
        posts.append({
            "id": n.get("id"), "caption": cap, "timestamp": dt.datetime.fromtimestamp(n.get("taken_at_timestamp") or 0, dt.timezone.utc).isoformat(),
            "like_count": (n.get("edge_liked_by") or n.get("edge_media_preview_like") or {}).get("count"),
            "comments_count": (n.get("edge_media_to_comment") or {}).get("count"),
            "media_type": kind, "media_product_type": "REELS" if n.get("product_type") == "clips" else "FEED",
            "thumbnail_url": n.get("thumbnail_src") or n.get("display_url"), "media_url": n.get("display_url"),
            "permalink": f"https://www.instagram.com/p/{n.get('shortcode')}/" if n.get("shortcode") else "",
        })
    return {"username": user.get("username"), "name": user.get("full_name"),
            "followers_count": (user.get("edge_followed_by") or {}).get("count"),
            "media_count": (user.get("edge_owner_to_timeline_media") or {}).get("count"), "posts": posts, "source": "perfil_publico"}


APIFY_URL = "https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items"
APIFY_RETRY_WAIT = 5  # segundos antes de reintentar si Apify da 502/503
APIFY_TYPES = {"Sidecar": "CAROUSEL_ALBUM", "Video": "VIDEO", "Image": "IMAGE"}


def apify_profile(username: str, since: dt.date | None, until: dt.date | None, token: str, max_posts: int = 150,
                  client: httpx.Client | None = None, followers_client: httpx.Client | None = None) -> dict[str, Any]:
    """Publicaciones de una cuenta pública vía Apify (Instagram Scraper), desde hoy hacia atrás hasta `since`.

    Apify cobra por publicación devuelta: `max_posts` pone el tope por cuenta (APIFY_MAX_POSTS en .env)."""
    c = client or httpx.Client(timeout=300)
    body = {"directUrls": [f"https://www.instagram.com/{username.lstrip('@')}/"], "resultsType": "posts",
            "resultsLimit": max_posts, "addParentData": False}
    if since:
        body["onlyPostsNewerThan"] = since.isoformat()
    resp = c.post(APIFY_URL, params={"token": token}, json=body)
    if resp.status_code >= 500:  # caída momentánea de Apify (502/503): un reintento
        import time

        time.sleep(APIFY_RETRY_WAIT)
        resp = c.post(APIFY_URL, params={"token": token}, json=body)
    if resp.status_code >= 400:
        try:
            msg = (resp.json().get("error") or {}).get("message") or resp.text[:200]
        except ValueError:
            msg = resp.text[:200]
        raise RuntimeError(f"Apify: {msg}")
    data = [it for it in resp.json() if isinstance(it, dict)]
    items = [it for it in data if it.get("timestamp") and not it.get("error")]
    errs = [it for it in data if it.get("error")]
    if not items and errs:  # cuenta que no existe, privada, etc.: mejor decirlo que mostrar 0 publicaciones
        e = errs[0]
        raise RuntimeError(f"Apify: @{username.lstrip('@')}: {e.get('errorDescription') or e.get('error')}")
    posts = [{
        "id": str(it.get("id") or it.get("shortCode")), "caption": it.get("caption") or "", "timestamp": it["timestamp"],
        "like_count": it["likesCount"] if (it.get("likesCount") or 0) >= 0 else None, "comments_count": it.get("commentsCount"),
        "media_type": APIFY_TYPES.get(it.get("type"), "IMAGE"), "media_product_type": "REELS" if it.get("productType") == "clips" else "FEED",
        "thumbnail_url": it.get("displayUrl"), "media_url": it.get("displayUrl"),
        "permalink": it.get("url") or (f"https://www.instagram.com/p/{it['shortCode']}/" if it.get("shortCode") else ""),
    } for it in items]
    reached = min((p["timestamp"][:10] for p in posts), default=None)
    posts = [p for p in posts if (not since or p["timestamp"][:10] >= since.isoformat()) and (not until or p["timestamp"][:10] <= until.isoformat())]
    followers = None
    try:  # seguidores: del perfil público (gratis); si Instagram no responde, de Apify (1 resultado)
        followers = public_profile(username, followers_client).get("followers_count")
    except Exception:
        try:
            det = c.post(APIFY_URL, params={"token": token}, json={k: v for k, v in {**body, "resultsType": "details", "resultsLimit": 1}.items()
                                                                   if k != "onlyPostsNewerThan"})
            followers = next((d.get("followersCount") for d in det.json() if isinstance(d, dict) and d.get("followersCount")), None)
        except Exception:
            pass
    return {"username": username.lstrip("@"), "name": (items[0].get("ownerFullName") if items else None), "followers_count": followers,
            "posts": posts, "source": "apify", "visible_since": reached, "capped": len(items) >= max_posts}


IG_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "ig_cache"  # compartido entre clientes


def _cache_path(username: str) -> Path:
    return IG_CACHE_DIR / f"{re.sub(r'[^a-z0-9._]', '_', username.lower().lstrip('@'))}.json"


def cached_only(username: str, since: dt.date, until: dt.date) -> dict[str, Any] | None:
    """Lo que ya está guardado de una cuenta entre dos fechas, sin llamar a Apify (sin costo). None si el archivo no
    llega a esas fechas."""
    import json

    path = _cache_path(username)
    if not path.exists():
        return None
    cache = json.loads(path.read_text(encoding="utf-8"))
    covered = cache.get("covered_since")
    if not covered or covered > since.isoformat():
        return None
    posts = [dict(x) for x in cache.get("posts") or [] if since.isoformat() <= x["timestamp"][:10] <= until.isoformat()]
    return {"username": cache.get("username"), "name": cache.get("name"), "followers_count": cache.get("followers_count"),
            "posts": posts, "source": "apify", "visible_since": covered, "capped": False}


def apify_cached(username: str, since: dt.date | None, until: dt.date | None, token: str, max_posts: int,
                 client: httpx.Client | None = None, followers_client: httpx.Client | None = None,
                 today: dt.date | None = None) -> dict[str, Any]:
    """Apify con memoria: cada publicación se paga una sola vez. Lo que ya se bajó (para cualquier cliente o guía)
    queda guardado; si alcanza para las fechas pedidas no se vuelve a llamar a Apify, y si faltan las más nuevas
    se piden solo esas."""
    import json

    today = today or dt.date.today()
    path = _cache_path(username)
    cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    posts = {p["id"]: p for p in cache.get("posts") or []}
    covered = cache.get("covered_since")  # desde qué fecha está todo
    fetched = cache.get("fetched_on")     # hasta qué día se bajó
    need_old = since and (not covered or covered > since.isoformat())
    need_new = not fetched or (until is None or until.isoformat() >= fetched)
    cost_posts = 0
    if need_old or need_new or not posts:
        # si ya hay historia completa hasta `since`, solo hacen falta las publicaciones desde la última bajada
        ask_since = since if need_old or not fetched else dt.date.fromisoformat(fetched)
        got = apify_profile(username, ask_since, None, token, max_posts, client, followers_client)
        cost_posts = len(got.get("posts") or [])
        for x in got["posts"]:
            posts[x["id"]] = {k: x.get(k) for k in (*RAW_KEYS, "thumbnail_url", "media_url")}
        reached = got.get("visible_since")
        if need_old:  # si se topó con el tope, solo está completo desde donde llegó
            covered = (reached if got.get("capped") and reached else ask_since.isoformat()) if ask_since else covered
        cache = {"username": username.lstrip("@"), "name": got.get("name") or cache.get("name"),
                 "followers_count": got.get("followers_count") or cache.get("followers_count"),
                 "covered_since": covered, "fetched_on": today.isoformat(), "posts": list(posts.values())}
        IG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    window = [dict(x) for x in posts.values() if (not since or x["timestamp"][:10] >= since.isoformat())
              and (not until or x["timestamp"][:10] <= until.isoformat())]
    capped = bool(since and covered and covered > since.isoformat())
    return {"username": username.lstrip("@"), "name": cache.get("name"), "followers_count": cache.get("followers_count"),
            "posts": window, "source": "apify", "visible_since": covered, "capped": capped, "paid_posts": cost_posts}


class InstagramOrganic:
    def __init__(self, token: str | None = None, client: httpx.Client | None = None, public_client: httpx.Client | None = None,
                 apify_client: httpx.Client | None = None):
        self.token = token or settings.instagram_access_token
        self.client = client or httpx.Client(timeout=60)
        self.version = settings.meta_graph_version
        self._ig_id = settings.instagram_business_id
        self.public_client = public_client
        self.apify_client = apify_client

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        resp = self.client.get(f"https://graph.facebook.com/{self.version}/{path}", params={"access_token": self.token, **params})
        data = resp.json() if resp.headers.get("content-type", "").startswith(("application/json", "text/javascript")) else {}
        if resp.status_code >= 400 or "error" in data:
            raise RuntimeError(f"Instagram: {(data.get('error') or {}).get('message', resp.text[:300])}")
        return data

    def own_accounts(self) -> dict[str, str]:
        """Cuentas de Instagram a las que el token tiene acceso directo (usuario en minúscula -> id)."""
        if not hasattr(self, "_own"):
            pages = self._get("me/accounts", fields="instagram_business_account{id,username}", limit=200).get("data", [])
            self._own = {p["instagram_business_account"]["username"].lower(): p["instagram_business_account"]["id"]
                         for p in pages if p.get("instagram_business_account", {}).get("username")}
        return self._own

    def ig_id(self) -> str:
        """La cuenta de Instagram profesional desde la que se consulta (la primera que tenga el token)."""
        if not self._ig_id:
            pages = self._get("me/accounts", fields="name,instagram_business_account{id,username}", limit=100).get("data", [])
            ig = next((p["instagram_business_account"] for p in pages if p.get("instagram_business_account")), None)
            if not ig:
                raise RuntimeError("El token no tiene ninguna cuenta de Instagram profesional vinculada a una página "
                                   "(faltan los permisos instagram_basic y pages_show_list, o la cuenta no está vinculada)")
            self._ig_id = ig["id"]
        return self._ig_id

    def profile(self, username: str, since: dt.date | None = None, until: dt.date | None = None, max_pages: int = 8) -> dict[str, Any]:
        """Perfil y publicaciones de una cuenta, desde las más nuevas hasta `since` (o hasta max_pages × 50).

        Si Meta no deja usar Business Discovery (competencia sin acceso avanzado), usa el perfil público: solo las
        últimas ~12 publicaciones."""
        try:
            return self._profile_api(username, since, until, max_pages)
        except RuntimeError:
            # cuenta propia del cliente: el error es real. De la competencia: Meta no deja (#10) o no hay cuenta
            # vinculada → Apify o el perfil público
            try:
                is_own = username.lower().lstrip("@") in self.own_accounts()
            except RuntimeError:
                is_own = False
            if is_own:
                raise
        if settings.apify_token:  # de la competencia, hasta la fecha del evento (pago por publicación)
            return apify_cached(username, since, until, settings.apify_token, settings.apify_max_posts, self.apify_client, self.public_client)
        prof = public_profile(username, self.public_client)
        oldest = min((p["timestamp"][:10] for p in prof["posts"]), default=None)
        prof["visible_since"] = oldest  # lo más viejo que deja ver Instagram sin iniciar sesión
        prof["posts"] = [p for p in prof["posts"] if (not since or p["timestamp"][:10] >= since.isoformat())
                         and (not until or p["timestamp"][:10] <= until.isoformat())]
        return prof

    def _profile_api(self, username: str, since: dt.date | None, until: dt.date | None, max_pages: int) -> dict[str, Any]:
        posts: list[dict[str, Any]] = []
        after = ""
        info: dict[str, Any] = {}
        # Cuentas propias (de los clientes): lectura directa, alcanza con instagram_basic.
        # De terceros: Business Discovery, que Meta solo habilita con acceso avanzado (revisión de la app).
        own_id = self.own_accounts().get(username.lower().lstrip("@"))
        for _ in range(max_pages):
            media = f"media{'.after(' + after + ')' if after else ''}.limit(50){{{POST_FIELDS}}}"
            if own_id:
                bd = self._get(own_id, fields=f"username,name,followers_count,media_count,{media}")
            else:
                bd = self._get(self.ig_id(), fields=f"business_discovery.username({username}){{username,name,followers_count,media_count,{media}}}")
                bd = bd.get("business_discovery") or {}
            info = info or {k: bd.get(k) for k in ("username", "name", "followers_count", "media_count")}
            page = (bd.get("media") or {})
            batch = page.get("data") or []
            posts += batch
            oldest = min((p["timestamp"][:10] for p in batch), default=None)
            after = ((page.get("paging") or {}).get("cursors") or {}).get("after", "")
            if not after or not batch or (since and oldest and oldest < since.isoformat()):
                break
        if since or until:
            posts = [p for p in posts if (not since or p["timestamp"][:10] >= since.isoformat())
                     and (not until or p["timestamp"][:10] <= until.isoformat())]
        return {**info, "posts": posts, "source": "api"}


def _save_image(p: dict[str, Any], assets: Path) -> None:
    url = p.get("thumbnail_url") or p.get("media_url")
    if url and not p.get("image"):
        try:
            path = assets / f"ig_{p['id']}.jpg"
            if not path.exists():
                path.write_bytes(httpx.get(url, timeout=30, follow_redirects=True).content)
            p["image"] = str(path)
        except Exception:
            pass


POST_KEYS = ("caption", "like_count", "comments_count", "interactions", "rate", "timestamp", "permalink", "media_type", "names_event", "image")


def event_block(posts: list[dict[str, Any]], event_days: tuple[dt.date, dt.date] | None, month_avg: float,
                assets: Path | None = None, top: int = 6) -> dict[str, Any]:
    """Las publicaciones DEL EVENTO (lo nombran o salieron en sus días) y cómo les fue contra el promedio de la cuenta
    en todo el período. Separa lo que se hizo para la fecha de lo que la cuenta publica siempre."""
    d0, d1 = (event_days or (None, None))
    picked = []
    for p in posts:
        day = (p.get("timestamp") or "")[:10]
        in_days = bool(d0 and d1 and d0.isoformat() <= day <= d1.isoformat())
        if p.get("names_event") or in_days:
            picked.append({**p, "in_days": in_days})
    picked.sort(key=lambda p: -p["interactions"])
    if assets:
        for p in picked[:top]:
            _save_image(p, assets)
    n = len(picked)
    avg = round(sum(p["interactions"] for p in picked) / n, 1) if n else 0
    return {"posts": n, "named": sum(1 for p in picked if p.get("names_event")), "in_days": sum(1 for p in picked if p["in_days"]),
            "avg_interactions": avg, "vs_period_pct": round(100 * (avg / month_avg - 1)) if n and month_avg else None,
            "pieces": [{**{k: p.get(k) for k in POST_KEYS}, "in_days": p["in_days"]} for p in picked[:top]]}


def summarize(profile: dict[str, Any], event_keywords: list[str] | None = None, top: int = 4,
              assets: Path | None = None, event_days: tuple[dt.date, dt.date] | None = None) -> dict[str, Any]:
    """Números de una cuenta en la ventana: publicaciones, promedio de likes y comentarios, interacción sobre seguidores,
    cuántas nombraron el evento y las de más interacción (con su imagen guardada). Con `event_days`, además el bloque
    de las publicaciones del evento en sí (`event`)."""
    from ..relevance import matches

    posts = profile.get("posts") or []
    followers = profile.get("followers_count") or 0
    for p in posts:
        p["interactions"] = (p.get("like_count") or 0) + (p.get("comments_count") or 0)
        p["rate"] = round(100 * p["interactions"] / followers, 2) if followers else None
        p["names_event"] = bool(event_keywords and matches(p.get("caption") or "", event_keywords))
    best = sorted(posts, key=lambda p: -p["interactions"])[:top]
    if assets:
        assets.mkdir(parents=True, exist_ok=True)
        for p in best:
            _save_image(p, assets)
    n = len(posts)
    types: dict[str, int] = {}
    for p in posts:
        t = "reel" if p.get("media_product_type") == "REELS" else {"CAROUSEL_ALBUM": "carrusel", "VIDEO": "video", "IMAGE": "imagen"}.get(p.get("media_type"), "otro")
        types[t] = types.get(t, 0) + 1
    return {
        "username": profile.get("username"), "name": profile.get("name"), "followers": followers, "posts": n,
        "source": profile.get("source") or "api", "visible_since": profile.get("visible_since"),
        "archived": profile.get("archived") or 0, "capped": bool(profile.get("capped")),
        "avg_likes": round(sum(p.get("like_count") or 0 for p in posts) / n, 1) if n else 0,
        "avg_comments": round(sum(p.get("comments_count") or 0 for p in posts) / n, 1) if n else 0,
        "avg_rate": round(sum(p["rate"] or 0 for p in posts) / n, 2) if n and followers else None,
        "event_posts": sum(p["names_event"] for p in posts),
        "types": types,
        "top": [{k: p.get(k) for k in POST_KEYS} for p in best],
        **({"event": event_block(posts, event_days, sum(p["interactions"] for p in posts) / n if n else 0, assets)}
           if event_keywords or event_days else {}),
    }


RAW_KEYS = ("id", "caption", "like_count", "comments_count", "timestamp", "media_type", "media_product_type", "permalink")


def raw_posts(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Lo mínimo de cada publicación para archivarla (sin las URLs de imagen, que vencen)."""
    return [{k: (p.get(k)[:600] if k == "caption" and p.get(k) else p.get(k)) for k in RAW_KEYS} for p in profile.get("posts") or []]


def merge_archive(profile: dict[str, Any], archived: list[dict[str, Any]], since: dt.date | None, until: dt.date | None) -> dict[str, Any]:
    """Suma al perfil las publicaciones guardadas en corridas anteriores (archivo propio del Radar) dentro de la ventana.
    Si una publicación está en los dos lados, gana la más nueva (likes actualizados)."""
    seen = {p.get("id") for p in profile.get("posts") or []}
    extra = [p for p in archived if p.get("id") not in seen and p.get("timestamp")
             and (not since or p["timestamp"][:10] >= since.isoformat()) and (not until or p["timestamp"][:10] <= until.isoformat())]
    return {**profile, "posts": (profile.get("posts") or []) + extra, "archived": len(extra)}
