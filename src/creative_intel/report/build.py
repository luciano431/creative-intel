"""Genera el reporte mensual en HTML (visual), Markdown y una versión HTML simplificada para Basecamp."""
from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).parent / "templates"

MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def period_label(period: str) -> str:
    y, m = period.split("-")
    return f"{MONTHS_ES[int(m) - 1].capitalize()} {y}"


def next_period_label(period: str) -> str:
    y, m = (int(x) for x in period.split("-"))
    m += 1
    if m > 12:
        m, y = 1, y + 1
    return f"{MONTHS_ES[m - 1].capitalize()} {y}"


def dots(n: int, max_n: int, width: int = 5) -> str:
    if max_n <= 0 or n <= 0:
        return "·"
    filled = max(1, round(width * n / max_n))
    return "●" * filled + "○" * (width - filled)


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    env.filters["dots"] = dots
    env.filters["bar"] = lambda pct: max(2, min(100, int(round(float(pct or 0)))))
    return env


def build_report_context(
    period: str,
    metrics: dict[str, Any],
    synthesis: dict[str, Any],
    items: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    by_id = {it["id"]: it for it in items}
    refs = []
    for r in synthesis.get("reference_creatives", []):
        it = by_id.get(r.get("creative_id"))
        if it:
            refs.append({**r, "item": it})
    matrix = metrics.get("angle_matrix", {})
    max_cell = max((v for row in matrix.values() for v in row.values()), default=0)
    tier_of = {it["competitor"]: it["tier"] for it in items}
    return {
        "period": period,
        "period_label": period_label(period),
        "next_period_label": next_period_label(period),
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "metrics": metrics,
        "synthesis": synthesis,
        "reference_creatives": refs,
        "concepts": concepts[:15],
        "items": sorted(items, key=lambda it: (it["tier"], it["competitor"], -it["days_active"])),
        "families": list(next(iter(matrix.values()), {}).keys()) if matrix else [],
        "matrix": matrix,
        "max_cell": max_cell,
        "tier_of": tier_of,
        "review": review or {},
        "top_longevity": [by_id[i] for i in metrics.get("longevity", {}).get("top_ids", []) if i in by_id][:5],
    }


def render_report(context: dict[str, Any], out_dir: str | Path, copy_assets: bool = True) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if copy_assets:
        assets_out = out / "assets"
        assets_out.mkdir(exist_ok=True)
        for it in context["items"]:
            p = it.get("thumbnail_path") or ((it.get("raw") or {}).get("screenshot") if isinstance(it.get("raw"), dict) else "")
            if p and Path(p).is_file():
                dest = assets_out / Path(p).name
                if not dest.exists():
                    shutil.copy(p, dest)
                it["thumbnail_rel"] = f"assets/{Path(p).name}"
    env = _env()
    outputs: dict[str, Path] = {}
    for tpl, name in (("report.html.j2", "report.html"), ("report.md.j2", "report.md"), ("basecamp.html.j2", "basecamp.html")):
        rendered = env.get_template(tpl).render(**context)
        path = out / name
        path.write_text(rendered, encoding="utf-8")
        outputs[name] = path
    return outputs
