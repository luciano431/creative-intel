"""Token de Meta: aviso antes de correr si está vencido y canje por uno de 60 días.

Un token sacado del Explorador de la API dura ~1-2 horas. Canjeado con la app (META_APP_ID + META_APP_SECRET)
dura 60 días. `radar renovar-token` hace el canje y lo guarda en .env.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import httpx

from .config import settings

HOW_TO_RENEW = ("Sacá uno nuevo en developers.facebook.com/tools/explorer (app del Radar, permisos ads_read, instagram_basic, "
                "pages_show_list, pages_read_engagement, business_management → Generate Access Token) y corré "
                "[radar renovar-token]: lo canjea por uno de 60 días y lo guarda en .env.")


def _graph(path: str, **params: Any) -> dict[str, Any]:
    resp = httpx.get(f"https://graph.facebook.com/{settings.meta_graph_version}/{path}", params=params, timeout=30)
    try:
        data = resp.json()
    except ValueError:
        data = {"error": {"message": resp.text[:200]}}
    if resp.status_code >= 400 or "error" in data:
        raise RuntimeError((data.get("error") or {}).get("message") or resp.text[:200])
    return data


def token_problem(token: str | None, label: str = "META_ACCESS_TOKEN") -> str | None:
    """None si el token anda; si no, por qué y cómo renovarlo."""
    if not token:
        return f"Falta {label} en .env. {HOW_TO_RENEW}"
    try:
        _graph("me", access_token=token, fields="id")
    except httpx.HTTPError as exc:
        return f"No se pudo hablar con Meta ({exc}). Revisá la conexión a internet."
    except RuntimeError as exc:
        msg = str(exc)
        if "expired" in msg.lower() or "session" in msg.lower():
            return f"El token de Meta ({label}) está vencido. {HOW_TO_RENEW}"
        return f"Meta rechazó {label}: {msg}. {HOW_TO_RENEW}"
    return None


def exchange(short_token: str) -> str:
    """Canjea un token corto por uno de 60 días (necesita META_APP_ID y META_APP_SECRET de la misma app)."""
    return _graph("oauth/access_token", grant_type="fb_exchange_token", client_id=settings.meta_app_id,
                  client_secret=settings.meta_app_secret, fb_exchange_token=short_token)["access_token"]


def token_info(token: str) -> dict[str, Any]:
    """Permisos y vencimiento del token."""
    data = _graph("debug_token", input_token=token, access_token=f"{settings.meta_app_id}|{settings.meta_app_secret}").get("data") or {}
    exp = data.get("expires_at") or data.get("data_access_expires_at") or 0
    return {"scopes": data.get("scopes") or [], "valid": bool(data.get("is_valid")),
            "expires": dt.datetime.fromtimestamp(exp).date() if exp else None}


def write_env(values: dict[str, str], path: Path) -> None:
    """Reemplaza (o agrega) las claves en .env sin tocar el resto."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    done = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in values and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={values[key]}"
            done.add(key)
    lines += [f"{k}={v}" for k, v in values.items() if k not in done]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
