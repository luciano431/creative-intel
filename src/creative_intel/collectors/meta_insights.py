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
INSIGHT_FIELDS = "ad_id,ad_name,spend,impressions,reach,clicks,ctr,actions,action_values,objective,optimization_goal,account_currency"
BASIC_FIELDS = "ad_id,ad_name,spend,impressions,clicks,ctr,actions,action_values,account_currency"  # por si la cuenta rechaza los de objetivo

# El resultado de cada anuncio sale del OBJETIVO de su campaña (lo que Meta optimiza), no de lo que haya pasado:
# una campaña de tráfico se mide en visitas a la web aunque alguien después haya dejado un lead.
GOAL_RESULT = {
    "LINK_CLICKS": ("clics al sitio", ("link_click",), "tofu"),
    "LANDING_PAGE_VIEWS": ("visitas a la web", ("landing_page_view", "omni_landing_page_view"), "tofu"),
    "REACH": ("alcance", (), "tofu"), "IMPRESSIONS": ("alcance", (), "tofu"), "AD_RECALL_LIFT": ("alcance", (), "tofu"),
    "THRUPLAY": ("reproducciones", ("video_view",), "tofu"), "VIDEO_VIEWS": ("reproducciones", ("video_view",), "tofu"),
    "POST_ENGAGEMENT": ("interacciones", ("post_engagement",), "mofu"),
    "CONVERSATIONS": ("conversaciones", ("onsite_conversion.messaging_conversation_started_7d",), "mofu"),
    "LEAD_GENERATION": ("leads", ("lead", "onsite_conversion.lead_grouped"), "bofu"),
    "QUALITY_LEAD": ("leads", ("lead", "onsite_conversion.lead_grouped"), "bofu"),
}
OBJECTIVE_RESULT = {
    "OUTCOME_AWARENESS": GOAL_RESULT["REACH"], "OUTCOME_TRAFFIC": GOAL_RESULT["LINK_CLICKS"],
    "OUTCOME_ENGAGEMENT": GOAL_RESULT["POST_ENGAGEMENT"], "OUTCOME_LEADS": GOAL_RESULT["LEAD_GENERATION"],
    "OUTCOME_SALES": ("compras", ("purchase", "offsite_conversion.fb_pixel_purchase", "omni_purchase"), "bofu"),
}
FUNNEL_LABEL = {"tofu": "que nos conozcan (TOFU)", "mofu": "consideración (MOFU)", "bofu": "conversión (BOFU)"}


def objective_result(ins: dict[str, Any]) -> tuple[str, float, str] | None:
    """Tipo de resultado, cantidad y etapa según el objetivo de la campaña; None si no se informa."""
    goal, objective = ins.get("optimization_goal") or "", ins.get("objective") or ""
    spec = GOAL_RESULT.get(goal)
    if goal == "OFFSITE_CONVERSIONS" or (not spec and objective):
        spec = OBJECTIVE_RESULT.get(objective)
    if not spec:
        return None
    label, keys, stage = spec
    if label == "alcance":
        return label, float(ins.get("reach") or ins.get("impressions") or 0), stage
    by = {a.get("action_type"): float(a.get("value") or 0) for a in ins.get("actions") or []}
    return label, next((by[k] for k in keys if by.get(k)), 0.0), stage
AD_FIELDS = "id,name,effective_status,created_time,creative{title,body,thumbnail_url,image_url,object_type,video_id}"


def pick_result(actions: list[dict[str, Any]] | None) -> tuple[str, float]:
    by = {a.get("action_type"): float(a.get("value") or 0) for a in actions or []}
    for key, label in RESULT_ACTIONS:
        if by.get(key):
            return label, by[key]
    return "", 0.0


# Cada tipo de resultado por separado (nunca se suman entre sí)
RESULT_TYPES = {
    "compras": ("purchase", "offsite_conversion.fb_pixel_purchase"),
    "conversaciones": ("onsite_conversion.messaging_conversation_started_7d",),
    "leads": ("lead", "offsite_conversion.fb_pixel_lead", "onsite_conversion.lead_grouped"),
    "clics al sitio": ("link_click",),
}


def results_by_type(actions: list[dict[str, Any]] | None) -> dict[str, float]:
    by = {a.get("action_type"): float(a.get("value") or 0) for a in actions or []}
    out = {}
    for label, keys in RESULT_TYPES.items():
        v = next((by[k] for k in keys if by.get(k)), 0.0)
        if v:
            out[label] = v
    return out


def performance_row(ins: dict[str, Any]) -> dict[str, Any]:
    spend = float(ins.get("spend") or 0)
    by_goal = objective_result(ins)
    label, results = (by_goal[0], by_goal[1]) if by_goal else pick_result(ins.get("actions"))
    stage = by_goal[2] if by_goal else ""
    values = {a.get("action_type"): float(a.get("value") or 0) for a in ins.get("action_values") or []}
    revenue = values.get("purchase") or values.get("offsite_conversion.fb_pixel_purchase") or 0.0
    return {
        "spend": round(spend, 2),
        "impressions": int(float(ins.get("impressions") or 0)),
        "clicks": int(float(ins.get("clicks") or 0)),
        "ctr": round(float(ins.get("ctr") or 0), 2),
        "result_type": label,
        "results": results,
        # alcance: costo cada 1.000 personas; el resto, costo por resultado
        "cost_per_result": (round(1000 * spend / results, 2) if label == "alcance" else round(spend / results, 2)) if results else None,
        "stage": stage, "objective": ins.get("objective") or "", "optimization_goal": ins.get("optimization_goal") or "",
        "roas": round(revenue / spend, 2) if revenue and spend else None,
        "by_type": results_by_type(ins.get("actions")),
        "currency": ins.get("account_currency") or "",
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
            rows = self._get(f"{acct}/insights", level="account", fields="spend,impressions,clicks,actions,action_values,account_currency", time_increment=1,
                             limit=500, time_range=f'{{"since":"{since.isoformat()}","until":"{until.isoformat()}"}}').get("data", [])
            for r in rows:
                perf = performance_row(r)
                d = days.setdefault(r["date_start"], {"date": r["date_start"], "spend": 0.0, "impressions": 0, "clicks": 0, "by_type": {},
                                                         "currency": r.get("account_currency") or ""})
                d["spend"] = round(d["spend"] + perf["spend"], 2)
                d["impressions"] += perf["impressions"]
                d["clicks"] += perf["clicks"]
                for k, v in perf["by_type"].items():
                    d["by_type"][k] = d["by_type"].get(k, 0) + v
        return [days[k] for k in sorted(days)]

    def collect(self, competitor: CompetitorConfig, since: dt.date | None = None, until: dt.date | None = None,
                limit: int | None = None) -> list[RawCreative]:
        window = since is not None
        until = until or dt.date.today()
        since = since or until - dt.timedelta(days=self.days)
        data: list[dict[str, Any]] = []
        time_range = f'{{"since":"{since.isoformat()}","until":"{until.isoformat()}"}}'
        for acct in self.accounts(competitor.meta_ad_account_id):  # varias cuentas: se suman (las sin gasto no aportan)
            try:
                data += self._get(f"{acct}/insights", level="ad", fields=INSIGHT_FIELDS, limit=500, time_range=time_range).get("data", [])
            except RuntimeError as exc:
                if "access token" in str(exc).lower():
                    raise
                print(f"[meta] {acct}: {exc} · reintento sin los campos de objetivo")
                data += self._get(f"{acct}/insights", level="ad", fields=BASIC_FIELDS, limit=500, time_range=time_range).get("data", [])
        top = sorted(data, key=lambda r: -float(r.get("spend") or 0))[: limit or self.limit]
        out: list[RawCreative] = []
        for ins in top:
            try:  # vista previa grande (sin esto Meta devuelve una miniatura de 64 px)
                ad = self._get(str(ins["ad_id"]), fields=AD_FIELDS.replace("creative{", "creative.thumbnail_width(600).thumbnail_height(600){"))
            except RuntimeError:
                try:
                    ad = self._get(str(ins["ad_id"]), fields=AD_FIELDS)
                except RuntimeError as exc:  # anuncio borrado o sin permiso: los resultados igual valen
                    print(f"[meta] anuncio {ins['ad_id']}: {exc} · entra solo con sus números")
                    ad = {}
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
                # Catálogo: el título es una plantilla ({{product.name}}); se usa el nombre del anuncio
                headline=(cr.get("title") if cr.get("title") and "{{" not in cr.get("title") else
                          ("Catálogo · " if "{{" in (cr.get("title") or "") else "") + (ins.get("ad_name") or "")),
                body_text="" if "{{" in (cr.get("body") or "") else (cr.get("body") or ""),
                thumbnail_url=cr.get("image_url") or cr.get("thumbnail_url") or "",
                snapshot_url="",
                countries=list(competitor.ad_countries or [competitor.country]),
                engagement={"performance": perf},
                first_seen=dt.date.fromisoformat(ad["created_time"][:10]) if ad.get("created_time") else None,
                is_active=ad.get("effective_status") == "ACTIVE",
                raw={"ad_name": ins.get("ad_name"), "ad_id": str(ins["ad_id"]), "performance": perf, "refresh_media": True,
                     "catalog": "{{" in (cr.get("title") or "") + (cr.get("body") or "")},
            ))
        self._previews(out)
        return out

    def _previews(self, raws: list[RawCreative]) -> None:
        """Captura nítida de cada anuncio tal como se ve en el feed (vista previa oficial de Meta).

        Sirve también para los anuncios de catálogo y los de marca, que no tienen una imagen fija.
        """
        import html as _html
        import os
        import re as _re

        targets = []
        for r in raws:
            try:
                body = (self._get(f"{r.raw['ad_id']}/previews", ad_format="MOBILE_FEED_STANDARD").get("data") or [{}])[0].get("body", "")
            except Exception:
                continue
            m = _re.search(r'src="([^"]+)"', body or "")
            if m:
                targets.append((r, _html.unescape(m.group(1))))
        if not targets:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return
        shots = settings.assets_dir / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_PATH") or None)
            page = browser.new_page(viewport={"width": 420, "height": 900}, device_scale_factor=2, locale="es-UY")
            for r, url in targets:
                try:
                    page.goto(url, wait_until="networkidle", timeout=45_000)
                    page.wait_for_timeout(1500)
                    path = shots / f"own_{r.raw['ad_id']}.png"
                    page.screenshot(path=str(path), full_page=True)
                    r.raw["screenshot"] = str(path)
                except Exception:
                    continue
            browser.close()
