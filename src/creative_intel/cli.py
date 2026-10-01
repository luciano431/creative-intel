"""CLI: creative-intel <comando>. Ver README para el flujo mensual."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from . import pipeline
from .config import load_competitors, load_taxonomy
from .db import init_db, session_scope, get_or_create_run
from .review import approve_all_pending, data_qa_queue, latest_synthesis, review_synthesis, set_qa_status

app = typer.Typer(help="Radar creativo de Ranker: monitoreo creativo de la competencia (Meta + Google) y plan creativo mensual por cliente. Para trabajar con otro cliente: RADAR_CLIENT=<slug> radar ...", no_args_is_help=True)
competitors_app = typer.Typer(help="Universo de competidores")
review_app = typer.Typer(help="Checkpoints humanos (Data QA y Strategic QA)")
basecamp_app = typer.Typer(help="Configuración de Basecamp (login OAuth e IDs)")
app.add_typer(competitors_app, name="competitors")
app.add_typer(review_app, name="review")
app.add_typer(basecamp_app, name="basecamp")
console = Console()

PeriodOpt = typer.Option(None, "--period", "-p", help="Mes analizado YYYY-MM (default: mes actual)")


def _period(p: Optional[str]) -> str:
    return p or pipeline.current_period()


def _print_point(i: int, p) -> None:
    if isinstance(p, dict):
        rprint(f"  [bold]{i}. {p.get('headline', '')}[/bold]")
        if p.get("detail"):
            rprint(f"     {p['detail']}")
        if p.get("action"):
            rprint(f"     [cyan]Qué hacemos:[/cyan] {p['action']}")
    else:
        rprint(f"  {i}. {p}")


@app.callback()
def _init() -> None:
    init_db()


@app.command("init-db")
def init_db_cmd() -> None:
    """Crea las tablas y sincroniza competidores desde config/competitors.yaml."""
    with session_scope() as s:
        n = pipeline.sync(s)
    rprint(f"[green]Base lista.[/green] {n} competidores sincronizados.")


@competitors_app.command("list")
def competitors_list() -> None:
    t = Table("slug", "nombre", "país", "tier", "meta_page_id", "google_advertiser_id", "activo")
    for c in load_competitors():
        t.add_row(c.slug, c.name, c.country, c.tier, c.meta_page_id or "[red]—[/red]", c.google_advertiser_id or "[red]—[/red]", "✓" if c.active else "✗")
    console.print(t)
    rprint("[dim]Para agregar competidores o países: editar config/competitors.yaml y correr `competitors sync`.[/dim]")


@competitors_app.command("sync")
def competitors_sync() -> None:
    with session_scope() as s:
        n = pipeline.sync(s)
    rprint(f"[green]{n} competidores sincronizados.[/green]")


@app.command()
def collect(
    period: Optional[str] = PeriodOpt,
    source: list[str] = typer.Option(None, "--source", "-s", help="meta_api | meta_web | google_serpapi | google_web | manual (repetible)"),
    only: list[str] = typer.Option(None, "--only", help="Limitar a estos slugs"),
    no_media: bool = typer.Option(False, "--no-media", help="No descargar thumbnails"),
) -> None:
    """Recolecta creativos de todas las fuentes configuradas."""
    with session_scope() as s:
        stats = pipeline.collect(s, _period(period), sources=source or None, only=only or None, download_media=not no_media)
    rprint(stats)


@app.command("import")
def import_cmd(
    file: Path = typer.Argument(..., exists=True, help="CSV o JSON (ver data/samples/)"),
    period: Optional[str] = PeriodOpt,
    competitor: Optional[str] = typer.Option(None, "--competitor", "-c", help="slug por defecto si el archivo no lo trae"),
    platform: Optional[str] = typer.Option(None, "--platform", help="meta | google por defecto"),
) -> None:
    """Importa creativos manualmente (incluye nuestros propios anuncios, tier=own)."""
    with session_scope() as s:
        stats = pipeline.import_file(s, _period(period), file, competitor, platform)
    rprint(stats)


@app.command()
def classify(
    period: Optional[str] = PeriodOpt,
    mock_ai: bool = typer.Option(False, "--mock-ai", help="Clasificador heurístico, sin llamadas a la API"),
    reclassify: bool = typer.Option(False, "--reclassify", help="Volver a clasificar los ya clasificados"),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Reintentar solo los que quedaron con la regla simple por un error de la API"),
) -> None:
    """Etapa 1: clasifica cada creativo dentro de la taxonomía cerrada."""
    with session_scope() as s:
        stats = pipeline.classify(s, _period(period), mock=mock_ai, reclassify=reclassify, retry_failed=retry_failed)
    rprint(stats)
    if stats.get("aborted"):
        rprint("[red]Clasificación detenida por un error de la API (crédito o clave).[/red] "
               "Lo ya clasificado quedó guardado; cuando lo resuelvas corré: "
               f"creative-intel classify --period {_period(period)} --retry-failed")
        raise typer.Exit(1)


@app.command()
def analyze(period: Optional[str] = PeriodOpt) -> None:
    """Métricas del mes + comparación con el mes anterior + white space + nosotros vs mercado."""
    with session_scope() as s:
        m = pipeline.analyze(s, _period(period))
    rprint(m["totals"])
    t = Table("familia de ángulo", "creativos", "%")
    for d in m["angle_family_dist"]:
        t.add_row(d["value"], str(d["count"]), f"{d['pct']}%")
    console.print(t)


@app.command()
def synthesize(period: Optional[str] = PeriodOpt, mock_ai: bool = typer.Option(False, "--mock-ai")) -> None:
    """Etapa 2: síntesis estratégica (borrador para validación humana)."""
    with session_scope() as s:
        synth = pipeline.synthesize_run(s, _period(period), mock=mock_ai)
        rprint(f"[green]Síntesis #{synth.id} creada[/green] (modelo {synth.model}, estado {synth.status}).")
        for i, p in enumerate(synth.data["executive_summary"], 1):
            _print_point(i, p)


@app.command()
def report(period: Optional[str] = PeriodOpt, out: Optional[Path] = typer.Option(None, "--out", "-o")) -> None:
    """Renderiza el reporte (HTML visual, Markdown y HTML para Basecamp)."""
    with session_scope() as s:
        paths = pipeline.build_report(s, _period(period), out)
    for name, p in paths.items():
        rprint(f"  {name}: {p}")


@review_app.command("data")
def review_data(period: Optional[str] = PeriodOpt, approve_rest: bool = typer.Option(False, "--approve-rest", help="Marcar OK todo lo no señalado")) -> None:
    """Checkpoint 1: lista los creativos que una persona debería validar."""
    with session_scope() as s:
        run = get_or_create_run(s, _period(period))
        queue = data_qa_queue(s, run)
        t = Table("id", "competidor", "plat.", "fuente", "días", "titular", "flags")
        for r in queue:
            t.add_row(str(r["id"]), r["competitor"], r["platform"], r["source"], str(r["days_active"]), r["headline"], ", ".join(r["flags"]))
        console.print(t)
        rprint(f"{len(queue)} creativos para revisar. Usar `review qa --ok 12,13 --reject 14` para resolver.")
        if approve_rest:
            n = approve_all_pending(s, run)
            rprint(f"[green]{n} creativos marcados OK.[/green]")


@review_app.command("qa")
def review_qa(
    ok: Optional[str] = typer.Option(None, help="ids separados por coma"),
    reject: Optional[str] = typer.Option(None, help="ids separados por coma (se excluyen del análisis)"),
    flag: Optional[str] = typer.Option(None, help="ids separados por coma"),
    notes: str = typer.Option("", help="nota"),
) -> None:
    """Checkpoint 1: resolver la cola de Data QA."""
    with session_scope() as s:
        for status, raw in (("ok", ok), ("rejected", reject), ("flagged", flag)):
            if raw:
                ids = [int(x) for x in raw.split(",") if x.strip()]
                rprint(f"{status}: {set_qa_status(s, ids, status, notes)} creativos")


@review_app.command("show")
def review_show(period: Optional[str] = PeriodOpt, export: Optional[Path] = typer.Option(None, "--export", help="Guardar la síntesis en JSON para editarla")) -> None:
    """Checkpoint 2: ver la síntesis pendiente de aprobación."""
    with session_scope() as s:
        run = get_or_create_run(s, _period(period))
        synth = latest_synthesis(s, run)
        if not synth:
            rprint("[red]No hay síntesis para este periodo.[/red]")
            raise typer.Exit(1)
        rprint(f"[bold]Síntesis #{synth.id}[/bold] · modelo {synth.model} · estado [cyan]{synth.status}[/cyan]")
        d = synth.data
        rprint("\n[bold]Resumen ejecutivo[/bold]")
        for i, p in enumerate(d["executive_summary"], 1):
            _print_point(i, p)
        rprint("\n[bold]Hallazgos clave[/bold]")
        for i in d["top_insights"]:
            rprint(f"  • {i['title']} [{i['confidence']}] — {i['observation']}")
        rprint("\n[bold]Oportunidades[/bold]")
        for o in d["white_space"] + d["recommendations"]:
            rprint(f"  • {o['title']}")
        rprint("\n[bold]Riesgos[/bold]")
        for r in d["risks"]:
            rprint(f"  • {r['title']}")
        if export:
            export.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            rprint(f"\nExportado a {export}. Editá y volvé con `review edit --file {export}`.")


@review_app.command("approve")
def review_approve(period: Optional[str] = PeriodOpt, reviewer: str = typer.Option(..., "--reviewer", "-r"), notes: str = typer.Option("", "--notes")) -> None:
    """Checkpoint 2: APPROVE."""
    with session_scope() as s:
        synth = review_synthesis(s, get_or_create_run(s, _period(period)), "approve", reviewer, notes)
    rprint(f"[green]Síntesis #{synth.id} aprobada.[/green] Ahora: `creative-intel publish`.")


@review_app.command("reject")
def review_reject(period: Optional[str] = PeriodOpt, reviewer: str = typer.Option(..., "--reviewer", "-r"), notes: str = typer.Option(..., "--notes")) -> None:
    """Checkpoint 2: REJECT (queda registrado; volver a correr synthesize)."""
    with session_scope() as s:
        synth = review_synthesis(s, get_or_create_run(s, _period(period)), "reject", reviewer, notes)
    rprint(f"[yellow]Síntesis #{synth.id} rechazada.[/yellow]")


@review_app.command("edit")
def review_edit(file: Path = typer.Argument(..., exists=True), period: Optional[str] = PeriodOpt, reviewer: str = typer.Option(..., "--reviewer", "-r"), notes: str = typer.Option("", "--notes")) -> None:
    """Checkpoint 2: EDIT (sube la versión editada como aprobada)."""
    edited = json.loads(file.read_text(encoding="utf-8"))
    with session_scope() as s:
        synth = review_synthesis(s, get_or_create_run(s, _period(period)), "edit", reviewer, notes, edited=edited)
    rprint(f"[green]Síntesis #{synth.id} (editada) aprobada.[/green]")


@app.command()
def publish(period: Optional[str] = PeriodOpt, force: bool = typer.Option(False, "--force", help="Publicar aunque no esté aprobada")) -> None:
    """Sube el reporte a Basecamp (documento + to-dos). Requiere síntesis aprobada."""
    with session_scope() as s:
        result = pipeline.publish(s, _period(period), force=force)
    rprint(result)


@basecamp_app.command("login")
def basecamp_login() -> None:
    """Login OAuth con Basecamp: imprime los valores para pegar en .env."""
    from .publish.basecamp_auth import login

    result = login()
    rprint("\n[green]Autorizado.[/green] Pegá esto en tu .env:\n")
    rprint(f"BASECAMP_ACCESS_TOKEN={result['access_token']}")
    rprint(f"BASECAMP_REFRESH_TOKEN={result['refresh_token']}")
    if len(result["accounts"]) == 1:
        rprint(f"BASECAMP_ACCOUNT_ID={result['accounts'][0]['id']}")
    else:
        rprint("\nCuentas disponibles (elegí una para BASECAMP_ACCOUNT_ID):")
        for a in result["accounts"]:
            rprint(f"  {a['id']}  {a['name']}")


@basecamp_app.command("projects")
def basecamp_projects() -> None:
    """Lista proyectos con sus IDs de Docs & Files (vault) y listas de tareas."""
    from .publish.basecamp_auth import list_projects

    for p in list_projects():
        rprint(f"[bold]{p['name']}[/bold]  BASECAMP_PROJECT_ID={p['project_id']}  BASECAMP_VAULT_ID={p['vault_id']}")
        for t in p["todolists"]:
            rprint(f"    lista '{t['name']}'  BASECAMP_TODOLIST_ID={t['id']}")


@app.command()
def purge(
    platform: Optional[str] = typer.Option(None, "--platform", help="meta | google"),
    only: list[str] = typer.Option([], "--only", help="slug de competidor (repetible)"),
    source: Optional[str] = typer.Option(None, "--source", help="meta_web, google_web, ..."),
    yes: bool = typer.Option(False, "--yes", "-y", help="No pedir confirmación"),
) -> None:
    """Borra creativos (con sus clasificaciones y avistamientos) para volver a recolectarlos limpios."""
    from .db import purge_creatives

    _init()
    if not (platform or only or source):
        raise typer.BadParameter("Indicá al menos --platform, --only o --source")
    if not yes:
        typer.confirm(f"Borrar creativos platform={platform} only={only or 'todos'} source={source}?", abort=True)
    with session_scope() as s:
        console.print(purge_creatives(s, platform=platform, slugs=only or None, source=source))


@app.command("new-client")
def new_client(
    slug: str = typer.Argument(..., help="Identificador corto, sin espacios (ej. banco-x)"),
    name: str = typer.Option(..., "--name", help="Nombre del cliente como aparece en el informe"),
    industry: str = typer.Option("", "--industry", help="Industria (ej. Seguros, Banca, Retail)"),
    country: str = typer.Option("UY", "--country", help="Código de país principal"),
    market: str = typer.Option("", "--market", help="Nombre del mercado (ej. Uruguay)"),
) -> None:
    """Crea la carpeta de configuración de un cliente nuevo (config/clients/<slug>/)."""
    import shutil

    from .config import CLIENTS_DIR, ROOT

    dest = CLIENTS_DIR / slug
    if dest.exists():
        raise typer.BadParameter(f"Ya existe {dest}")
    dest.mkdir(parents=True)
    (dest / "competitors.yaml").write_text(f"""# Cliente y universo de competidores de {name}.
# Completar meta_page_id (Biblioteca de anuncios de Meta) y website / google_domain (Google).
client:
  slug: {slug}
  name: {name}
  industry: {industry}
  market: {market or country}
  description: ""

countries:
  - code: {country}
    name: {market or country}
    languages: [es]
    own:
      slug: {slug}
      name: {name}
      website: ""
      meta_page_id: ""
      google_advertiser_id: ""
      products: []
    competitors:
      local:
        - slug: competidor-1
          name: Competidor 1
          website: ""
          meta_page_id: ""
          google_advertiser_id: ""
          products: []
          active: true
      global:
        - slug: referente-1
          name: Referente global 1
          website: ""
          meta_page_id: ""
          google_advertiser_id: ""
          ad_countries: [ALL]
          products: []
          active: true
""", encoding="utf-8")
    shutil.copy(ROOT / "config" / "taxonomy.yaml", dest / "taxonomy.yaml")
    console.print(f"[green]Cliente creado en {dest}[/green]")
    console.print("1. Completá competitors.yaml (IDs de Meta, dominios de Google).")
    console.print("2. Ajustá taxonomy.yaml si la industria no es seguros (productos, mensajes).")
    console.print(f"3. Usalo con: [bold]RADAR_CLIENT={slug} radar init-db && RADAR_CLIENT={slug} radar competitors sync[/bold]")


@app.command("debug-html")
def debug_html(file: Path = typer.Argument(..., exists=True), top: int = typer.Option(40, help="cantidad de etiquetas a listar")) -> None:
    """Resume un HTML guardado por el modo diagnóstico: etiquetas más frecuentes, links a creativos, textos visibles."""
    import re
    from collections import Counter

    html = file.read_text(encoding="utf-8", errors="ignore")
    tags = Counter(m.group(1).lower() for m in re.finditer(r"<([a-zA-Z][\w-]*)", html))
    rprint(f"[bold]{file}[/bold]  {len(html):,} bytes")
    rprint("Etiquetas más frecuentes:", ", ".join(f"{t}:{n}" for t, n in tags.most_common(top)))
    custom = [t for t in tags if "-" in t]
    rprint("Elementos custom:", ", ".join(sorted(custom)[:60]))
    links = sorted(set(re.findall(r"href=\"([^\"]*creative[^\"]*)\"", html)))
    rprint(f"Links con 'creative': {len(links)}", links[:5])
    classes = Counter(c for m in re.finditer(r"class=\"([^\"]+)\"", html) for c in m.group(1).split() if "creativ" in c.lower() or "card" in c.lower() or "grid" in c.lower())
    rprint("Clases con creativ/card/grid:", classes.most_common(30))
    texts = [t.strip() for t in re.findall(r">([^<>{}]{25,160})<", html)]
    rprint("Textos visibles (muestra):")
    for t in texts[:25]:
        rprint("  ·", t)


@app.command("run-cycle")
def run_cycle(
    period: Optional[str] = PeriodOpt,
    mock_ai: bool = typer.Option(False, "--mock-ai"),
    skip_collect: bool = typer.Option(False, "--skip-collect"),
    source: list[str] = typer.Option(None, "--source", "-s"),
    out: Optional[Path] = typer.Option(None, "--out", "-o"),
    no_notify: bool = typer.Option(False, "--no-notify"),
) -> None:
    """Ciclo completo hasta el borrador (collect → classify → analyze → synthesize → report). No publica."""
    with session_scope() as s:
        summary = pipeline.run_cycle(s, _period(period), mock_ai=mock_ai, skip_collect=skip_collect, sources=source or None, out_dir=out, send_notifications=not no_notify)
    rprint(summary)


@app.command()
def status(period: Optional[str] = PeriodOpt) -> None:
    """Estado del ciclo del mes."""
    with session_scope() as s:
        run = get_or_create_run(s, _period(period))
        rprint({"period": run.period, "status": run.status, "stats": run.stats, "totals": (run.metrics or {}).get("totals")})


@app.command()
def demo(out: Path = typer.Option(Path("reports/demo"), "--out", "-o")) -> None:
    """Corre dos meses con datos ficticios y sin IA para ver el reporte completo (incluye mes vs mes)."""
    from .collectors import load_manual_file
    from .db import Run

    sample = Path(__file__).resolve().parents[2] / "data" / "samples" / "demo_creatives.json"
    raws = load_manual_file(sample)
    today = dt.date.today()
    cur = today.strftime("%Y-%m")
    prev = pipeline.previous_period(cur)
    prev_end = dt.date(int(cur[:4]), int(cur[5:]), 1) - dt.timedelta(days=1)
    with session_scope() as s:
        pipeline.sync(s)
        # Mes anterior: solo los creativos que ya existían al cierre de ese mes
        prev_raws = [r for r in raws if r.first_seen and r.first_seen <= prev_end]
        for r in prev_raws:
            r.last_seen = None
        prev_run = get_or_create_run(s, prev)
        from .normalize import upsert_creatives

        upsert_creatives(s, prev_run, prev_raws, prev_end)
        pipeline.classify(s, prev, mock=True)
        pipeline.analyze(s, prev)
        # Mes actual: todo
        cur_raws = load_manual_file(sample)
        pipeline.collect(s, cur, sources=["manual"], extra_raws=cur_raws, download_media=False, seen_on=today)
        summary = pipeline.run_cycle(s, cur, mock_ai=True, skip_collect=True, out_dir=out, send_notifications=False)
    rprint(summary)
    rprint(f"\n[green]Abrí {out / 'report.html'} en el navegador.[/green]")


if __name__ == "__main__":
    app()
