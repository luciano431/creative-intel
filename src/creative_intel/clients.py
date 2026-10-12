"""Clientes: alta (carpeta de configuración) y listado para la web.

El cliente por defecto vive en `config/`; los demás en `config/clients/<slug>/`. Cada uno tiene su
base de datos y sus informes. Los comandos eligen cliente con la variable RADAR_CLIENT.
"""
from __future__ import annotations

import datetime as dt
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import ROOT, load_client, load_competitors

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")

TEMPLATE = """# Cliente y universo de competidores de {name}.
# Completar meta_page_id (Biblioteca de anuncios de Meta) y website / google_domain (Google).
client:
  slug: {slug}
  name: {name}
  industry: {industry}
  market: {market}
  description: ""
  # Hoja del cliente: la IA la respeta al escribir ideas y acciones. Completar lo que se sepa.
  brief:
    tono: ""              # cómo habla la marca (voseo, cercano, técnico...)
    publico: ""           # a quién le hablamos
    vender_mas: []        # productos o servicios a empujar
    diferencial: ""
    promos: []            # promociones posibles; vacío = no inventar descuentos
    evitar: ""            # lo que NO quieren
    notas: ""
  # Rubro: de las marcas con "generalist: true" (Sodimac, Mercado Libre...) y de lo encontrado por palabra clave
  # solo se toman los anuncios que mencionan alguna de estas palabras (sin tildes; "=" adelante = palabra exacta)
  focus_keywords: []

countries:
  - code: {country}
    name: {market}
    languages: [es]
    own:
      slug: {slug}
      name: {name}
      website: ""
      meta_page_id: ""          # ID de la página en la Biblioteca de anuncios de Meta
      google_domain: ""         # dominio para Google Ads Transparency (ej. misegurook.com)
      google_advertiser_id: ""
      meta_ad_account_id: ""    # cuenta publicitaria del cliente (act_...) para traer sus resultados reales
      products: []
    competitors:
      local:
        - slug: competidor-1
          name: Competidor 1
          website: ""
          meta_page_id: ""
          google_domain: ""
          google_advertiser_id: ""
          products: []
          active: true
      global:
        - slug: referente-1
          name: Referente global 1
          website: ""
          meta_page_id: ""
          google_domain: ""     # o un dominio por país: {{ES: marca.es, IT: marca.it}}
          google_advertiser_id: ""
          ad_countries: [ALL]
          products: []
          active: true
"""


def scaffold_client(slug: str, name: str, industry: str = "", country: str = "UY", market: str = "",
                    clients_dir: Path | None = None) -> Path:
    """Crea config/clients/<slug>/ con competitors.yaml de plantilla y una copia de la taxonomía."""
    if not SLUG_RE.match(slug or ""):
        raise ValueError("El identificador va en minúsculas, sin espacios ni tildes (ej. banco-x)")
    if not (name or "").strip():
        raise ValueError("Falta el nombre del cliente")
    from . import config

    dest = (clients_dir or config.CLIENTS_DIR) / slug
    if dest.exists():
        raise ValueError(f"Ya existe {dest}")
    dest.mkdir(parents=True)
    country = (country or "UY").upper()
    (dest / "competitors.yaml").write_text(
        TEMPLATE.format(slug=slug, name=name.strip(), industry=industry.strip(), market=(market or country).strip(), country=country),
        encoding="utf-8",
    )
    shutil.copy(ROOT / "config" / "taxonomy.yaml", dest / "taxonomy.yaml")
    return dest


@dataclass
class ClientEntry:
    key: str              # identificador en la web (slug)
    env: str              # valor de RADAR_CLIENT ("" = cliente por defecto en config/)
    name: str
    industry: str
    market: str
    config_path: Path
    reports_dir: Path
    competitors: list[Any] = field(default_factory=list)

    @property
    def local(self) -> list[Any]:
        return [c for c in self.competitors if c.tier == "local"]

    @property
    def global_(self) -> list[Any]:
        return [c for c in self.competitors if c.tier == "global"]

    @property
    def own(self) -> Any:
        return next((c for c in self.competitors if c.tier == "own"), None)

    def missing_ids(self) -> list[Any]:
        from .collectors.google_web import domain_of

        # Falta de verdad solo si no hay forma de buscarlo en ninguna plataforma (muchas marcas chicas no tienen web
        # ni pautan en Google: eso es un dato, no un faltante). Las pausadas no cuentan.
        return [c for c in self.competitors if getattr(c, "active", True) and not c.meta_page_id and not (c.google_advertiser_id or domain_of(c))]

    def periods(self) -> list[str]:
        if not self.reports_dir.is_dir():
            return []
        return sorted((d.name for d in self.reports_dir.iterdir() if PERIOD_RE.match(d.name) and (d / "report.html").exists()), reverse=True)

    def event_reports(self) -> list[dict[str, str]]:
        d = self.reports_dir / "eventos"
        if not d.is_dir():
            return []
        rel = self.reports_dir.relative_to(ROOT / "reports")
        out = []
        for x in sorted(d.iterdir(), reverse=True):
            f = x / "report.html"
            if f.exists():
                mtime = int(f.stat().st_mtime)
                # ?v=: cada versión nueva tiene otra dirección, así el navegador no muestra la vieja guardada
                out.append({"name": x.name, "updated": dt.datetime.fromtimestamp(mtime).strftime("%d/%m %H:%M"),
                            "url": "/reports/" + "/".join([*rel.parts, "eventos", x.name, "report.html"]) + f"?v={mtime}"})
        return out

    def report_url(self, period: str) -> str:
        rel = self.reports_dir.relative_to(ROOT / "reports")
        return "/reports/" + "/".join([*rel.parts, period, "report.html"])


def _entry(key: str, env: str, config_path: Path, reports_dir: Path) -> ClientEntry | None:
    try:
        ci = load_client(config_path)
        comps = load_competitors(config_path)
    except Exception:
        return None
    return ClientEntry(key=key, env=env, name=ci.name, industry=ci.industry, market=ci.market,
                       config_path=config_path, reports_dir=reports_dir, competitors=comps)


def list_clients(root: Path | None = None) -> list[ClientEntry]:
    root = root or ROOT
    out: list[ClientEntry] = []
    default_cfg = root / "config" / "competitors.yaml"
    if default_cfg.exists():
        e = _entry("", "", default_cfg, root / "reports")
        if e:
            e.key = load_client(default_cfg).slug or "default"
            out.append(e)
    clients_dir = root / "config" / "clients"
    if clients_dir.is_dir():
        for d in sorted(clients_dir.iterdir()):
            if (d / "competitors.yaml").exists() and SLUG_RE.match(d.name) and all(c.key != d.name for c in out):
                e = _entry(d.name, d.name, d / "competitors.yaml", root / "reports" / d.name)
                if e:
                    out.append(e)
    return out


def get_client(key: str, root: Path | None = None) -> ClientEntry | None:
    return next((c for c in list_clients(root) if c.key == key), None)
