"""Alertas para los checkpoints humanos: Slack (incoming webhook) y/o webhook de Make."""
from __future__ import annotations

from typing import Any

import httpx

from ..config import settings


def notify(event: str, text: str, payload: dict[str, Any] | None = None) -> list[str]:
    """Envía a todos los canales configurados. Devuelve lista de canales que recibieron."""
    sent: list[str] = []
    body = {"event": event, "text": text, **(payload or {})}
    if settings.slack_webhook_url:
        try:
            httpx.post(settings.slack_webhook_url, json={"text": f"*[creative-intel] {event}*\n{text}"}, timeout=20).raise_for_status()
            sent.append("slack")
        except httpx.HTTPError:
            pass
    if settings.make_webhook_url:
        try:
            httpx.post(settings.make_webhook_url, json=body, timeout=20).raise_for_status()
            sent.append("make")
        except httpx.HTTPError:
            pass
    return sent
