"""Publicación en Basecamp 3/4 vía API REST.

- Documento en el Vault (Docs & Files) del proyecto con el reporte HTML simplificado.
- Capturas de creativos subidas como attachments y embebidas con <bc-attachment sgid>.
- Un to-do por action item en la lista configurada.

Auth: OAuth2 de 37signals. Con BASECAMP_REFRESH_TOKEN + CLIENT_ID/SECRET el token se
renueva solo; si solo hay BASECAMP_ACCESS_TOKEN se usa tal cual.
Docs: https://github.com/basecamp/bc3-api
"""
from __future__ import annotations

import datetime as dt
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from ..config import settings

TOKEN_URL = "https://launchpad.37signals.com/authorization/token"


class BasecampClient:
    def __init__(
        self,
        account_id: str | None = None,
        access_token: str | None = None,
        user_agent: str | None = None,
        client: httpx.Client | None = None,
    ):
        self.account_id = account_id or settings.basecamp_account_id
        self.access_token = access_token or settings.basecamp_access_token
        self.user_agent = user_agent or settings.basecamp_user_agent
        self.client = client or httpx.Client(timeout=60)
        if not self.account_id:
            raise ValueError("Falta BASECAMP_ACCOUNT_ID")
        if not self.access_token and settings.basecamp_refresh_token:
            self.access_token = self.refresh_access_token()
        if not self.access_token:
            raise ValueError("Falta BASECAMP_ACCESS_TOKEN (o refresh token + client id/secret)")

    @property
    def base(self) -> str:
        return f"https://3.basecampapi.com/{self.account_id}"

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}", "User-Agent": self.user_agent, "Content-Type": content_type}

    def refresh_access_token(self) -> str:
        resp = self.client.post(
            TOKEN_URL,
            params={
                "type": "refresh",
                "refresh_token": settings.basecamp_refresh_token,
                "client_id": settings.basecamp_client_id,
                "client_secret": settings.basecamp_client_secret,
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def upload_attachment(self, path: str | Path) -> str:
        """Sube un archivo y devuelve el sgid para embeber en rich text."""
        p = Path(path)
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        resp = self.client.post(
            f"{self.base}/attachments.json",
            params={"name": p.name},
            headers=self._headers(mime) | {"Content-Length": str(p.stat().st_size)},
            content=p.read_bytes(),
        )
        resp.raise_for_status()
        return resp.json()["attachable_sgid"]

    def create_document(self, project_id: str, vault_id: str, title: str, content_html: str) -> dict[str, Any]:
        resp = self.client.post(
            f"{self.base}/buckets/{project_id}/vaults/{vault_id}/documents.json",
            headers=self._headers(),
            json={"title": title, "content": content_html, "status": "active"},
        )
        resp.raise_for_status()
        return resp.json()

    def create_todo(self, project_id: str, todolist_id: str, content: str, description: str = "", due_on: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"content": content, "description": description}
        if due_on:
            body["due_on"] = due_on
        resp = self.client.post(
            f"{self.base}/buckets/{project_id}/todolists/{todolist_id}/todos.json",
            headers=self._headers(),
            json=body,
        )
        resp.raise_for_status()
        return resp.json()


def publish_to_basecamp(
    context: dict[str, Any],
    render_basecamp_html,
    client: BasecampClient | None = None,
    project_id: str | None = None,
    vault_id: str | None = None,
    todolist_id: str | None = None,
    create_todos: bool = True,
) -> dict[str, Any]:
    """Sube capturas, arma el documento y crea los to-dos. Devuelve {document_url, todo_ids}."""
    client = client or BasecampClient()
    project_id = project_id or settings.basecamp_project_id
    vault_id = vault_id or settings.basecamp_vault_id
    todolist_id = todolist_id or settings.basecamp_todolist_id
    if not (project_id and vault_id):
        raise ValueError("Faltan BASECAMP_PROJECT_ID / BASECAMP_VAULT_ID")

    for it in (context.get("local_top") or []) + (context.get("global_top") or []):
        raw = it.get("raw") if isinstance(it.get("raw"), dict) else {}
        p = it.get("thumbnail_path") or (raw or {}).get("screenshot")
        if p and Path(p).is_file():
            try:
                it["attachment_sgid"] = client.upload_attachment(p)
            except httpx.HTTPError:
                it["attachment_sgid"] = ""
    html = render_basecamp_html(context)
    client_name = getattr(context.get("client"), "name", "") or "cliente"
    title = f"Plan creativo para {client_name} — {context['next_period_label']}"
    doc = client.create_document(project_id, vault_id, title, html)
    todo_ids: list[int] = []
    if create_todos and todolist_id:
        today = dt.date.today()
        for a in context.get("actions") or context["synthesis"].get("action_items", []):
            due = (today + dt.timedelta(days=int(a.get("due_in_days", 7)))).isoformat()
            notes = (
                f"{a['description']}\n\nResponsable sugerido: {a['owner_role']}"
                + (f"\nEntregable: {a['deliverable']}" if a.get("deliverable") else "")
                + (f"\nCómo medimos: {a['success_metric']}" if a.get("success_metric") else "")
                + f"\nInforme: {doc.get('app_url', '')}"
            )
            todo = client.create_todo(project_id, todolist_id, f"[{context['next_period_label']}] {a['title']}", notes, due)
            todo_ids.append(todo["id"])
    return {"document_url": doc.get("app_url", ""), "document_id": doc.get("id"), "todo_ids": todo_ids}
