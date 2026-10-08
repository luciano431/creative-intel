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


class InstagramOrganic:
    def __init__(self, token: str | None = None, client: httpx.Client | None = None):
        self.token = token or settings.meta_access_token
        self.client = client or httpx.Client(timeout=60)
        self.version = settings.meta_graph_version
        self._ig_id = settings.instagram_business_id

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        resp = self.client.get(f"https://graph.facebook.com/{self.version}/{path}", params={"access_token": self.token, **params})
        data = resp.json() if resp.headers.get("content-type", "").startswith(("application/json", "text/javascript")) else {}
        if resp.status_code >= 400 or "error" in data:
            raise RuntimeError(f"Instagram: {(data.get('error') or {}).get('message', resp.text[:300])}")
        return data

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
        """Perfil y publicaciones de una cuenta pública, desde las más nuevas hasta `since` (o hasta max_pages × 50)."""
        posts: list[dict[str, Any]] = []
        after = ""
        info: dict[str, Any] = {}
        for _ in range(max_pages):
            media = f"media{'.after(' + after + ')' if after else ''}.limit(50){{{POST_FIELDS}}}"
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
        return {**info, "posts": posts}


def summarize(profile: dict[str, Any], event_keywords: list[str] | None = None, top: int = 4,
              assets: Path | None = None) -> dict[str, Any]:
    """Números de una cuenta en la ventana: publicaciones, promedio de likes y comentarios, interacción sobre seguidores,
    cuántas nombraron el evento y las de más interacción (con su imagen guardada)."""
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
            url = p.get("thumbnail_url") or p.get("media_url")
            if url and not p.get("image"):
                try:
                    path = assets / f"ig_{p['id']}.jpg"
                    if not path.exists():
                        path.write_bytes(httpx.get(url, timeout=30, follow_redirects=True).content)
                    p["image"] = str(path)
                except Exception:
                    pass
    n = len(posts)
    types: dict[str, int] = {}
    for p in posts:
        t = "reel" if p.get("media_product_type") == "REELS" else {"CAROUSEL_ALBUM": "carrusel", "VIDEO": "video", "IMAGE": "imagen"}.get(p.get("media_type"), "otro")
        types[t] = types.get(t, 0) + 1
    return {
        "username": profile.get("username"), "name": profile.get("name"), "followers": followers, "posts": n,
        "avg_likes": round(sum(p.get("like_count") or 0 for p in posts) / n, 1) if n else 0,
        "avg_comments": round(sum(p.get("comments_count") or 0 for p in posts) / n, 1) if n else 0,
        "avg_rate": round(sum(p["rate"] or 0 for p in posts) / n, 2) if n and followers else None,
        "event_posts": sum(p["names_event"] for p in posts),
        "types": types,
        "top": [{k: p.get(k) for k in ("caption", "like_count", "comments_count", "interactions", "rate", "timestamp", "permalink",
                                        "media_type", "names_event", "image")} for p in best],
    }
