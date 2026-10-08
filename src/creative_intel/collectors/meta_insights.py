"""Resultados reales de los anuncios del CLIENTE (Marketing API, cuenta publicitaria propia).

Necesita META_ACCESS_TOKEN con permiso ads_read sobre la cuenta (token de usuario del sistema del
Business Manager) y `meta_ad_account_id: act_...` en el cliente (sección own de competitors.yaml).

Trae los anuncios con más inversión del último período y sus métricas: inversión, impresiones, clics,
CTR, resultados (compras, leads, conversaciones o clics, en ese orden de preferencia), costo por
resultado y ROAS si hay compras. Cada anuncio entra como creativo propio para clasificarlo igual que
a la competencia y poder decir qué mensajes y formatos le funcionan al cliente.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import httpx

from ..config import CompetitorConfig, settings
from .base import RawCreative

# Qué cuenta como "resultado", en orden de preferencia
RESULT_ACTIONS = [
    ("purchase", "compras"), ("offsite_conversion.fb_pixel_purchase", "compras"),
    ("lead", "leads"), ("offsite_conversion.fb_pixel_lead", "leads"), ("onsite_conversion.lead_grouped", "leads"),
    ("onsite_conversion.messaging_conversation_started_7d", "conversaciones"),
    ("link_click", "clics al sitio"),
]
INSIGHT_FIELDS = "ad_id,ad_name,spend,impressions,clicks,ctr,actions,action_values"
AD_FIELDS = "id,name,effective_status,created_time,creative{title,body,thumbnail_url,image_url,object_type,video_id}"


def pick_result(actions: list[dict[str, Any]] | None) -> tuple[str, float]:
    by = {a.get("action_type"): float(a.get("value") or 0) for a in actions or []}
    for key, label in RESULT_ACTIONS:
        if by.get(key):
            return label, by[key]
    return "", 0.0


def performance_row(ins: dict[str, Any]) -> dict[str, Any]:
    spend = float(ins.get("spend") or 0)
    label, results = pick_result(ins.get("actions"))
    values = {a.get("action_type"): float(a.get("value") or 0) for a in ins.get("action_values") or []}
    revenue = values.get("purchase") or values.get("offsite_conversion.fb_pixel_purchase") or 0.0
    return {
        "spend": round(spend, 2),
        "impressions": int(float(ins.get("impressions") or 0)),
        "clicks": int(float(ins.get("clicks") or 0)),
        "ctr": round(float(ins.get("ctr") or 0), 2),
        "result_type": label,
        "results": results,
        "cost_per_result": round(spend / results, 2) if results else None,
        "roas": round(revenue / spend, 2) if revenue and spend else None,
    }


class MetaInsightsCollector:
    name = "meta_insights"
    platform = "meta"

    def __init__(self, token: str | None = None, days: int = 30, limit: int = 25, client: httpx.Client | None = None):
        self.token = token or settings.meta_access_token
        self.days = days
        self.limit = limit
        self.client = client or httpx.Client(timeout=60)
        self.version = settings.meta_graph_version

    def supports(self, competitor: CompetitorConfig) -> bool:
        return bool(self.token and competitor.is_own and competitor.meta_ad_account_id)

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        resp = self.client.get(f"https://graph.facebook.com/{self.version}/{path}", params={"access_token": self.token, **params})
        if resp.status_code >= 400:
            msg = (resp.json().get("error") or {}).get("message", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            raise RuntimeError(f"Meta Marketing API: {msg}")
        return resp.json()

    @staticmethod
    def accounts(value: str) -> list[str]:
        """Una o varias cuentas separadas por coma ("act_1, act_2" o solo los números)."""
        return [a if a.startswith("act_") else f"act_{a}" for a in (x.strip() for x in value.split(",")) if a]

    def collect_window(self, competitor: CompetitorConfig, country: str, date_from, date_to, limit: int) -> list[RawCreative]:
        """Los anuncios propios con más inversión dentro de una ventana pasada (ej. el CyberLunes anterior), con sus resultados."""
        if not self.supports(competitor) or date_from is None or date_to is None:
            return []
        return self.collect(competitor, since=date_from, until=date_to, limit=limit)

    def daily(self, competitor: CompetitorConfig, since: dt.date, until: dt.date) -> list[dict[str, Any]]:
        """Inversión y resultados del cliente día por día (todas sus cuentas sumadas)."""
        days: dict[str, dict[str, Any]] = {}
        for acct in self.accounts(competitor.meta_ad_account_id):
            rows = self._get(f"{acct}/insights", level="account", fields="spend,impressions,clicks,actions,action_values", time_increment=1,
                             limit=500, time_range=f'{{"since":"{since.isoformat()}","until":"{until.isoformat()}"}}').get("data", [])
            for r in rows:
                perf = performance_row(r)
                d = days.setdefault(r["date_start"], {"date": r["date_start"], "spend": 0.0, "impressions": 0, "clicks": 0, "results": 0.0,
                                                      "result_type": perf["result_type"]})
                d["spend"] = round(d["spend"] + perf["spend"], 2)
                d["impressions"] += perf["impressions"]
                d["clicks"] += perf["clicks"]
                d["results"] += perf["results"]
                d["result_type"] = d["result_type"] or perf["result_type"]
        return [days[k] for k in sorted(days)]

    def collect(self, competitor: CompetitorConfig, since: dt.date | None = None, until: dt.date | None = None,
                limit: int | None = None) -> list[RawCreative]:
        window = since is not None
        until = until or dt.date.today()
        since = since or until - dt.timedelta(days=self.days)
        data: list[dict[str, Any]] = []
        for acct in self.accounts(competitor.meta_ad_account_id):  # varias cuentas: se suman (las sin gasto no aportan)
            data += self._get(f"{acct}/insights", level="ad", fields=INSIGHT_FIELDS, limit=500,
                              time_range=f'{{"since":"{since.isoformat()}","until":"{until.isoformat()}"}}').get("data", [])
        top = sorted(data, key=lambda r: -float(r.get("spend") or 0))[: limit or self.limit]
        out: list[RawCreative] = []
        for ins in top:
            ad = self._get(str(ins["ad_id"]), fields=AD_FIELDS)
            cr = ad.get("creative") or {}
            perf = performance_row(ins)
            perf["period"] = {"since": since.isoformat(), "until": until.isoformat()}
            out.append(RawCreative(
                competitor_slug=competitor.slug,
                platform="meta",
                source="meta_insights",
                # En una ventana pasada se guarda aparte: los resultados son los de esas fechas
                external_id=f"own-{ins['ad_id']}" + (f"-{since.isoformat()}" if window else ""),
                format="video" if cr.get("video_id") or cr.get("object_type") == "VIDEO" else "imagen",
                headline=cr.get("title") or ins.get("ad_name") or "",
                body_text=cr.get("body") or "",
                thumbnail_url=cr.get("image_url") or cr.get("thumbnail_url") or "",
                snapshot_url="",
                countries=list(competitor.ad_countries or [competitor.country]),
                engagement={"performance": perf},
                first_seen=dt.date.fromisoformat(ad["created_time"][:10]) if ad.get("created_time") else None,
                is_active=ad.get("effective_status") == "ACTIVE",
                raw={"ad_name": ins.get("ad_name"), "performance": perf},
            ))
        return out
