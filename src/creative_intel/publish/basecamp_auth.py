"""Ayudas para configurar Basecamp sin pelearse con OAuth a mano.

- `login()`: abre el navegador, recibe el código en http://localhost:8765/callback y devuelve
  access_token + refresh_token + cuentas disponibles.
- `list_projects()`: para cada proyecto, los IDs que necesita .env (project, vault, todolist).

Requiere una app registrada en https://launchpad.37signals.com/integrations con
Redirect URI = http://localhost:8765/callback
"""
from __future__ import annotations

import http.server
import threading
import urllib.parse
import webbrowser
from typing import Any

import httpx

from ..config import settings

AUTH_URL = "https://launchpad.37signals.com/authorization/new"
TOKEN_URL = "https://launchpad.37signals.com/authorization/token"
INFO_URL = "https://launchpad.37signals.com/authorization.json"
REDIRECT_URI = "http://localhost:8765/callback"


def _wait_for_code(timeout: int = 300) -> str:
    holder: dict[str, str] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            holder["code"] = (qs.get("code") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Listo. Ya podés volver a la terminal.</h2>".encode())
            done.set()

        def log_message(self, *_):  # silencio
            pass

    server = http.server.HTTPServer(("localhost", 8765), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    done.wait(timeout)
    server.shutdown()
    if not holder.get("code"):
        raise TimeoutError("No llegó el código de autorización (¿la Redirect URI de la app es http://localhost:8765/callback?)")
    return holder["code"]


def login(client_id: str | None = None, client_secret: str | None = None, open_browser: bool = True) -> dict[str, Any]:
    client_id = client_id or settings.basecamp_client_id
    client_secret = client_secret or settings.basecamp_client_secret
    if not (client_id and client_secret):
        raise ValueError("Faltan BASECAMP_CLIENT_ID / BASECAMP_CLIENT_SECRET en .env")
    url = AUTH_URL + "?" + urllib.parse.urlencode({"type": "web_server", "client_id": client_id, "redirect_uri": REDIRECT_URI})
    print(f"Abrí esta URL si el navegador no se abre solo:\n{url}\n")
    if open_browser:
        webbrowser.open(url)
    code = _wait_for_code()
    resp = httpx.post(
        TOKEN_URL,
        params={"type": "web_server", "client_id": client_id, "client_secret": client_secret, "redirect_uri": REDIRECT_URI, "code": code},
        timeout=30,
    )
    resp.raise_for_status()
    tokens = resp.json()
    info = httpx.get(INFO_URL, headers={"Authorization": f"Bearer {tokens['access_token']}", "User-Agent": settings.basecamp_user_agent}, timeout=30)
    info.raise_for_status()
    accounts = [{"id": a["id"], "name": a["name"], "product": a.get("product")} for a in info.json().get("accounts", []) if a.get("product") in ("bc3", "bc4", None)]
    return {"access_token": tokens["access_token"], "refresh_token": tokens.get("refresh_token", ""), "accounts": accounts}


def list_projects(account_id: str | None = None, access_token: str | None = None) -> list[dict[str, Any]]:
    account_id = account_id or settings.basecamp_account_id
    access_token = access_token or settings.basecamp_access_token
    if not (account_id and access_token):
        raise ValueError("Faltan BASECAMP_ACCOUNT_ID / BASECAMP_ACCESS_TOKEN (correr `basecamp login` primero)")
    headers = {"Authorization": f"Bearer {access_token}", "User-Agent": settings.basecamp_user_agent}
    base = f"https://3.basecampapi.com/{account_id}"
    out: list[dict[str, Any]] = []
    url: str | None = f"{base}/projects.json"
    with httpx.Client(timeout=30, headers=headers) as c:
        while url:
            r = c.get(url)
            r.raise_for_status()
            for p in r.json():
                dock = {d["name"]: d for d in p.get("dock", []) if d.get("enabled")}
                vault = dock.get("vault", {}).get("id")
                todoset = dock.get("todoset", {}).get("id")
                todolists: list[dict[str, Any]] = []
                if todoset:
                    tl = c.get(f"{base}/buckets/{p['id']}/todosets/{todoset}/todolists.json")
                    if tl.status_code == 200:
                        todolists = [{"id": t["id"], "name": t["name"]} for t in tl.json()]
                out.append({"project_id": p["id"], "name": p["name"], "vault_id": vault, "todolists": todolists})
            url = _next_link(r.headers.get("Link", ""))
    return out


def _next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None
