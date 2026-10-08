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
    for c in [x for x in load_competitors() if x.active]:
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
    from .ai.client import usage

    rprint(stats)
    if not mock_ai:
        rprint(usage.summary())
    if stats.get("aborted"):
        rprint(f"[red]Clasificación detenida: {stats['aborted']}.[/red] "
               "Lo ya clasificado quedó guardado; para seguir, corré: "
               f"radar classify --period {_period(period)} --retry-failed")
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
    from .ai.client import api_problem, usage

    try:
        with session_scope() as s:
            usage.reset()
            synth = pipeline.synthesize_run(s, _period(period), mock=mock_ai)
            synth_id, synth_model, synth_status, points = synth.id, synth.model, synth.status, synth.data["executive_summary"]
    except Exception as exc:
        problem = api_problem(exc)
        if not problem:
            raise
        rprint(f"[red]No se pudo usar la IA.[/red] {problem}")
        raise typer.Exit(1)
    rprint(f"[green]Síntesis #{synth_id} creada[/green] (modelo {synth_model}, estado {synth_status}).")
    if not mock_ai:
        rprint(usage.summary())
    for i, p in enumerate(points, 1):
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
            rprint(f"  • {i['title']} — {i['observation']}")
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
    from .clients import scaffold_client

    try:
        dest = scaffold_client(slug, name, industry, country, market)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]Cliente creado en {dest}[/green]")
    console.print("1. Completá competitors.yaml (IDs de Meta, dominios de Google), o usá [bold]radar fill-ids[/bold] y [bold]radar discover[/bold].")
    console.print("2. Ajustá taxonomy.yaml si la industria no es seguros (productos, mensajes).")
    console.print(f"3. Usalo con: [bold]export RADAR_CLIENT={slug}[/bold], luego [bold]radar init-db[/bold] y [bold]radar competitors sync[/bold]")
    console.print("4. Informe del mes: [bold]radar run-cycle --period AAAA-MM[/bold] (agregá --mock-ai si no hay crédito de API)")


def _pages_table(pages, title: str, limit: int = 8) -> Table:
    t = Table(title=title)
    for col in ("#", "Página", "ID de Meta", "Anuncios vistos", "Me gusta", "Dominio de sus anuncios", "Parecido"):
        t.add_column(col)
    for i, pg in enumerate(pages[:limit], 1):
        d = pg.as_dict()
        t.add_row(str(i), d["name"] or "?", d["page_id"], str(d["ads"]), f"{d['likes']:,}".replace(",", ".") if d["likes"] else "—",
                  ", ".join(d["domains"]) or "—", f"{int(d['score'] * 100)}%" if d["score"] else "—")
    return t


@app.command("find-ids")
def find_ids(
    name: str = typer.Argument(..., help="Nombre de la marca, como aparece en Facebook/Instagram"),
    country: str = typer.Option("UY", "--country", "-c", help="País donde buscar en Meta (ALL = todos)"),
    domain: str = typer.Option("", "--domain", help="Dominio a verificar en Google (si no, se usa el de sus anuncios)"),
    region: str = typer.Option("", "--region", help="Región de Google (por defecto, el mismo país)"),
    no_google: bool = typer.Option(False, "--no-google", help="No verificar en Google"),
    show: bool = typer.Option(False, "--show", help="Mostrar el navegador"),
) -> None:
    """Busca el ID de la página de Meta y verifica el dominio en Google para una marca."""
    from .discovery import find_meta_pages, google_check, is_confident

    pages = find_meta_pages(name, country, headless=not show)
    if not pages:
        rprint(f"[yellow]No encontré anuncios de «{name}» en la Biblioteca de Meta ({country}).[/yellow] Probá con otro nombre o con --country ALL.")
    else:
        console.print(_pages_table(pages, f"Meta · «{name}» en {country}"))
        best = pages[0]
        rprint(("[green]Coincidencia clara:[/green] " if is_confident(best, name) else "[yellow]Revisar a mano:[/yellow] ")
               + f"{best.name} → meta_page_id: \"{best.page_id}\"")
    dom = domain or (pages[0].top_domain if pages else "")
    if no_google or not dom:
        if not no_google:
            rprint("[yellow]Sin dominio para verificar en Google.[/yellow] Pasalo con --domain marca.com")
        return
    g = google_check(dom, region or (country if country.upper() != "ALL" else "anywhere"), headless=not show)
    if g["declared"] or g["creatives_seen"]:
        rprint(f"[green]Google:[/green] {dom} ({g['region']}) → ~{g['declared'] or g['creatives_seen']} anuncios"
               + (f" · anunciante {', '.join(g['advertisers'])}" if g["advertisers"] else "") + f"\n  google_domain: {dom}")
    else:
        rprint(f"[yellow]Google:[/yellow] {dom} ({g['region']}) no muestra anuncios. Puede que no pauten en Google en esa región o que usen otro dominio.\n  {g['url']}")


@app.command()
def discover(
    keywords: str = typer.Option(..., "--keywords", "-k", help="Palabras del rubro separadas por coma (ej. \"seguros,seguro de auto\")"),
    country: list[str] = typer.Option(["UY"], "--country", "-c", help="País (repetible: -c ES -c AR -c MX)"),
    top: int = typer.Option(5, "--top", help="Cuántos proponer"),
    add: str = typer.Option("", "--add", help="local | global: agregarlos al competitors.yaml del cliente actual"),
    show: bool = typer.Option(False, "--show", help="Mostrar el navegador"),
) -> None:
    """Propone competidores: los anunciantes que más pautan con esas palabras en Meta."""
    from .config import CONFIG_DIR
    from .discovery import add_competitors, discover_advertisers

    words = [k.strip() for k in keywords.split(",") if k.strip()]
    known = [c.meta_page_id for c in load_competitors() if c.meta_page_id]
    pages = [p for p in discover_advertisers(words, country, exclude=known, headless=not show) if p.ads]
    if not pages:
        rprint("[yellow]No encontré anunciantes con esas palabras.[/yellow] Probá con términos más amplios.")
        raise typer.Exit(1)
    console.print(_pages_table(pages, f"Quién más pauta con «{', '.join(words)}» en {', '.join(country)}", limit=max(top * 2, 10)))
    rprint("[dim]Ojo: pueden aparecer comparadores, medios o bancos que no son competencia directa. Elegí a mano si hace falta.[/dim]")
    if add:
        if add not in ("local", "global"):
            raise typer.BadParameter("--add tiene que ser local o global")
        added = add_competitors(CONFIG_DIR / "competitors.yaml", pages[:top], add, country[0] if add == "local" else "")
        rprint(f"[green]Agregados como {add}:[/green] {', '.join(added) or 'ninguno nuevo'}  (copia del archivo anterior en competitors.yaml.bak)")


@app.command("fill-ids")
def fill_ids(
    write: bool = typer.Option(False, "--write", help="Guardar en competitors.yaml los que coinciden claramente"),
    check_google: bool = typer.Option(True, "--check-google/--no-check-google", help="Verificar los dominios en Google"),
    show: bool = typer.Option(False, "--show", help="Mostrar el navegador"),
) -> None:
    """Completa los IDs que faltan (Meta y dominio de Google) del cliente actual y sus competidores."""
    from .config import CONFIG_DIR
    from .discovery import find_meta_pages, google_check, is_confident, meta_page_domain, update_ids
    from .collectors.google_web import domain_of

    t = Table(title="IDs del cliente y sus competidores")
    for col in ("Marca", "Tipo", "Meta", "Google", "Estado"):
        t.add_column(col)
    updates: dict[str, dict[str, str]] = {}
    for c in load_competitors():
        country = (c.ad_countries or [c.country])[0]
        meta_id, meta_note = c.meta_page_id, "ya estaba" if c.meta_page_id else ""
        dom = domain_of(c, country)
        status = "ok"
        if not meta_id:
            pages = find_meta_pages(c.name, country, headless=not show)
            if pages and is_confident(pages[0], c.name):
                meta_id, meta_note = pages[0].page_id, f"{pages[0].name}"
                updates.setdefault(c.slug, {})["meta_page_id"] = meta_id
                if not dom and pages[0].top_domain:
                    dom = pages[0].top_domain
                    updates[c.slug]["google_domain"] = dom
            elif pages:
                meta_note, status = f"¿{pages[0].name} ({pages[0].page_id})?", "revisar"
            else:
                meta_note, status = "sin anuncios en Meta", "revisar"
        elif not dom:
            dom = meta_page_domain(meta_id, country, headless=not show)
            if dom:
                updates.setdefault(c.slug, {})["google_domain"] = dom
        g_note = "—"
        if dom and check_google:
            g = google_check(dom, country if country.upper() != "ALL" else "anywhere", headless=not show)
            n = g["declared"] or g["creatives_seen"]
            g_note = f"{dom} · ~{n} anuncios" if n else f"{dom} · sin anuncios"
        elif dom:
            g_note = dom
        else:
            status = "revisar" if status == "ok" else status
            g_note = "sin dominio"
        t.add_row(c.name, c.tier, f"{meta_id or '—'} {('(' + meta_note + ')') if meta_note else ''}", g_note,
                  "[green]ok[/green]" if status == "ok" else "[yellow]revisar[/yellow]")
    console.print(t)
    if write and updates:
        n = update_ids(CONFIG_DIR / "competitors.yaml", updates)
        rprint(f"[green]{n} datos guardados en {CONFIG_DIR / 'competitors.yaml'}[/green] (copia del anterior en competitors.yaml.bak)")
    elif updates:
        rprint("Para guardar los que coinciden claramente, volvé a correrlo con [bold]--write[/bold].")


@app.command("set")
def set_cmd(
    slug: str = typer.Argument(..., help="slug de la marca (ver competitors.yaml)"),
    name: Optional[str] = typer.Option(None, "--name", help="Nombre a mostrar"),
    meta: Optional[str] = typer.Option(None, "--meta", help="ID de la página de Meta (\"\" para borrarlo)"),
    domain: Optional[str] = typer.Option(None, "--domain", help="Dominio para Google (\"\" para borrarlo)"),
    website: Optional[str] = typer.Option(None, "--website", help="Web"),
    countries: Optional[str] = typer.Option(None, "--countries", help="Países donde buscar sus anuncios, ej. ALL o ES,IT"),
    ad_account: Optional[str] = typer.Option(None, "--ad-account", help="Solo el cliente: cuenta(s) publicitaria(s) para traer sus resultados (act_1 o act_1,act_2)"),
    instagram: Optional[str] = typer.Option(None, "--instagram", help="Usuario de Instagram (sin @) para el análisis orgánico"),
) -> None:
    """Cambia a mano los datos de un competidor (o del cliente) del cliente actual."""
    from .config import CONFIG_DIR
    from .discovery import set_fields

    fields: dict[str, object] = {}
    if name is not None:
        fields["name"] = name
    if meta is not None:
        fields["meta_page_id"] = meta.strip()
    if domain is not None:
        fields["google_domain"] = domain.strip().lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").strip("/")
    if website is not None:
        fields["website"] = website.strip()
    if countries is not None:
        fields["ad_countries"] = [c.strip().upper() for c in countries.split(",") if c.strip()]
    if ad_account is not None:
        from .collectors.meta_insights import MetaInsightsCollector

        fields["meta_ad_account_id"] = ", ".join(MetaInsightsCollector.accounts(ad_account))
    if instagram is not None:
        fields["instagram"] = instagram.strip().lstrip("@").removeprefix("https://www.instagram.com/").strip("/")
    if not fields:
        raise typer.BadParameter("Indicá al menos un dato: --name, --meta, --domain, --website, --countries, --ad-account o --instagram")
    try:
        entry = set_fields(CONFIG_DIR / "competitors.yaml", slug, fields)
    except KeyError:
        slugs = ", ".join(c.slug for c in load_competitors())
        raise typer.BadParameter(f"No existe «{slug}». Los que hay: {slugs}")
    rprint(f"[green]{entry.get('name')}[/green]: " + ", ".join(f"{k} = {v!r}" for k, v in fields.items()))


@app.command()
def web(
    port: int = typer.Option(8080, "--port", help="Puerto"),
    host: str = typer.Option("127.0.0.1", "--host", help="127.0.0.1 = solo esta compu; 0.0.0.0 = toda la red local"),
    public: bool = typer.Option(False, "--public", help="Link público https (Cloudflare) para entrar desde cualquier lado; pide WEB_PASSWORD"),
    no_open: bool = typer.Option(False, "--no-open", help="No abrir el navegador"),
) -> None:
    """Abre la web del Radar: elegir cliente, generar y ver informes, buscar IDs y competidores."""
    import os
    import re
    import shutil
    import subprocess
    import threading
    import webbrowser

    import uvicorn

    password = os.getenv("WEB_PASSWORD", "")
    if (public or host not in ("127.0.0.1", "localhost")) and len(password) < 10:
        rprint("[red]Para abrir la web fuera de esta compu hace falta una contraseña de al menos 10 caracteres.[/red]\n"
               "Cargala en .env con:  [bold]read -s \"P?Contraseña: \" && echo \"WEB_PASSWORD=$P\" >> .env[/bold]")
        raise typer.Exit(1)
    tunnel = None
    if public:
        exe = shutil.which("cloudflared")
        if not exe:
            rprint("[red]Falta cloudflared.[/red] Instalalo una vez con:  [bold]brew install cloudflared[/bold]")
            raise typer.Exit(1)
        tunnel = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        def watch() -> None:
            shown = False
            for line in tunnel.stdout:  # type: ignore[union-attr]
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m and not shown:
                    shown = True
                    rprint(f"\n[bold green]Link público: {m.group(0)}[/bold green]\n"
                           "Pide la contraseña de WEB_PASSWORD (el usuario puede ser cualquiera). "
                           "El link cambia cada vez que lo abrís y deja de andar al cerrar esto o apagar la Mac.\n")

        threading.Thread(target=watch, daemon=True).start()
        host = "127.0.0.1"

    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}"
    console.print(f"[green]Radar creativo en {url}[/green]  (Ctrl+C para cerrar)")
    if not no_open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run("creative_intel.web:create_app", factory=True, host=host, port=port, log_level="warning")
    finally:
        if tunnel:
            tunnel.terminate()


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
    from .ai.client import ApiUnavailable, api_problem

    try:
        with session_scope() as s:
            summary = pipeline.run_cycle(s, _period(period), mock_ai=mock_ai, skip_collect=skip_collect, sources=source or None, out_dir=out, send_notifications=not no_notify)
    except pipeline.NoData as exc:
        rprint(f"[red]Sin datos.[/red] {exc}")
        raise typer.Exit(1)
    except Exception as exc:
        problem = str(exc) if isinstance(exc, ApiUnavailable) else api_problem(exc)
        if not problem:
            raise
        rprint(f"[red]No se pudo usar la IA.[/red] {problem}")
        raise typer.Exit(1)
    rprint(summary)
    if not mock_ai:
        from .ai.client import usage

        rprint(usage.summary())


@app.command()
def audit(period: Optional[str] = PeriodOpt, only: Optional[str] = typer.Option(None, "--only", help="slug de un competidor")) -> None:
    """Muestra qué se bajó de cada competidor (para revisar que los datos sean los correctos)."""
    from .config import load_competitors, settings

    with session_scope() as s:
        run = get_or_create_run(s, _period(period))
        items = pipeline.items_for(s, run, load_taxonomy())
    target = settings.top_ads_per_brand
    for c in load_competitors():
        if only and c.slug != only:
            continue
        mine = [it for it in items if it["competitor"] == c.name]
        n_meta = sum(it["platform"] == "meta" for it in mine)
        n_google = sum(it["platform"] == "google" for it in mine)
        flag = "[green]OK[/green]" if c.is_own or (n_meta >= target and n_google >= target) else "[red]faltan datos[/red]"
        rprint(f"\n[bold]{c.name}[/bold] ({c.tier}{', solo rubro del cliente' if c.generalist else ''}) · Meta {n_meta}/{target} · Google {n_google}/{target} · {flag}")
        rprint(f"  Meta ID: {c.meta_page_id or '—'} · Google: {c.google_advertiser_id or c.google_domain or c.website or '—'} · países: {', '.join(c.ad_countries)}")
        from .analysis.metrics import start_known

        for it in mine:
            img = "con imagen" if it.get("thumbnail_path") else "sin imagen"
            text = " ".join(((it.get("headline") or "") + " " + (it.get("body_text") or "")).split())[:90]
            text = text or ("(el texto está dentro de la imagen)" if it.get("thumbnail_path") else "(sin texto)")
            days = f"{it['days_active']:>4} días" if start_known(it) else "   ? días"
            rprint(f"  · {it['platform']:6} {it['status']:8} {days} · {img} · {text}")


@app.command()
def evento(
    key: str = typer.Argument(..., help="Evento de events.yaml (ej. cyberlunes, black-friday)"),
    edition: Optional[str] = typer.Option(None, "--edition", "-e", help="Edición a estudiar (por defecto, la última cargada)"),
    mock_ai: bool = typer.Option(False, "--mock-ai", help="Sin IA: prueba el circuito sin gastar"),
    skip_collect: bool = typer.Option(False, "--skip-collect", help="Usar los anuncios del evento ya bajados"),
    live: bool = typer.Option(False, "--live", help="Edición en curso: lo que está activo ahora (correrlo varias veces antes y durante el evento)"),
    inicio: Optional[str] = typer.Option(None, "--inicio", help="Primer día de una edición pasada que no esté cargada (AAAA-MM-DD)"),
    fin: Optional[str] = typer.Option(None, "--fin", help="Último día de esa edición (AAAA-MM-DD; por defecto, igual al inicio)"),
) -> None:
    """Guía de un evento comercial: cómo lo encara el mercado (edición en curso o archivada) y qué hace el cliente.

    Edición pasada: lo que Google guarda de cada marca en esas fechas y los resultados reales del cliente (Meta).
    """
    from .ai.client import ApiUnavailable, api_problem, usage
    from .config import load_events
    from .events import run_event

    events = load_events()
    if key not in events:
        raise typer.BadParameter(f"No existe «{key}». Los que hay: {', '.join(events) or 'ninguno (ver config/events.yaml)'}")
    ev = events[key]
    try:
        from .events import live_edition

        if inicio:
            import datetime as _dt

            from .config import EventEdition

            d0 = _dt.date.fromisoformat(inicio)
            d1 = _dt.date.fromisoformat(fin) if fin else d0
            # Ventana: 3 semanas antes (anticipo) a 1 semana después (extensiones)
            ed = EventEdition(label=f"{d0:%Y-%m}", date_from=d0 - _dt.timedelta(days=21), date_to=d1 + _dt.timedelta(days=7), start=d0, end=d1)
        else:
            ed = live_edition(ev) if live else ev.edition(edition)
        usage.reset()
        with session_scope() as s:
            summary = run_event(s, ev, ed, mock_ai=mock_ai, skip_collect=skip_collect)
    except (pipeline.NoData, ValueError) as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    except Exception as exc:
        problem = str(exc) if isinstance(exc, ApiUnavailable) else api_problem(exc)
        if not problem:
            raise
        rprint(f"[red]No se pudo usar la IA.[/red] {problem}")
        raise typer.Exit(1)
    rprint(summary)
    if not mock_ai:
        rprint(usage.summary())


@app.command()
def eventos() -> None:
    """Lista los eventos cargados y sus ediciones."""
    from .config import load_events

    for k, ev in load_events().items():
        eds = ", ".join(f"{e.label} ({e.start or 'pico'})" for e in ev.editions)
        rprint(f"[bold]{k}[/bold] · {ev.name} · palabras: {', '.join(ev.keywords)} · ediciones: {eds} · próxima: {ev.next_start or 'sin confirmar'}")


@app.command("all")
def run_all(
    step: str = typer.Argument(..., help="ids = completar IDs · informe = informe del mes · audit = revisar datos · evento:<clave> = guía de un evento"),
    period: Optional[str] = PeriodOpt,
    only: list[str] = typer.Option([], "--only", help="identificador de cliente (repetible); por defecto, todos"),
) -> None:
    """Corre un paso para todos los clientes, uno tras otro (cada uno con su RADAR_CLIENT)."""
    import os
    import subprocess
    import sys

    from .clients import list_clients

    commands = {"ids": ["fill-ids", "--write"], "informe": ["run-cycle", "--no-notify"], "audit": ["audit"]}
    if step.startswith("evento:"):
        args = ["evento", step.split(":", 1)[1]]
    elif step not in commands:
        raise typer.BadParameter("Usá: ids, informe, audit o evento:<clave> (ej. evento:cyberlunes)")
    else:
        args = commands[step] + (["--period", period] if period and step != "ids" else [])
    failed = []
    for c in list_clients():
        if only and c.key not in only:
            continue
        rprint(f"\n[bold]══ {c.name} ══[/bold]")
        env = {**os.environ}
        env.pop("RADAR_CLIENT", None)
        if c.env:
            env["RADAR_CLIENT"] = c.env
        if subprocess.run([sys.executable, "-m", "creative_intel.cli", *args], env=env).returncode != 0:
            failed.append(c.name)
    rprint("\n[green]Listo.[/green]" if not failed else f"\n[red]Con error:[/red] {', '.join(failed)}")


@app.command("check-meta")
def check_meta() -> None:
    """Confirma el token de Meta (META_ACCESS_TOKEN) y lista las cuentas publicitarias a las que tiene acceso."""
    import httpx

    from .config import settings

    if not settings.meta_access_token:
        rprint("[red]Falta META_ACCESS_TOKEN en .env.[/red] Se crea en business.facebook.com → Configuración → Usuarios del sistema, "
               "con permiso ads_read y acceso a las cuentas publicitarias de los clientes.")
        raise typer.Exit(1)
    url = f"https://graph.facebook.com/{settings.meta_graph_version}/me/adaccounts"
    resp = httpx.get(url, params={"access_token": settings.meta_access_token, "fields": "name,account_id,account_status", "limit": 200}, timeout=60)
    if resp.status_code >= 400:
        rprint(f"[red]Meta rechazó el token:[/red] {resp.json().get('error', {}).get('message', resp.text)}")
        raise typer.Exit(1)
    for a in resp.json().get("data", []):
        rprint(f"  act_{a['account_id']}  {a.get('name', '')}")
    rprint("Cargá la del cliente con:  [bold]radar set <slug-del-cliente> --ad-account act_...[/bold]")


@app.command("check-api")
def check_api_cmd() -> None:
    """Confirma que la clave de la IA (OpenAI o Claude) anda y que la cuenta tiene saldo (cuesta una fracción de centavo)."""
    from .ai.client import ApiUnavailable, check_api
    from .config import settings

    try:
        check_api()
    except ApiUnavailable as exc:
        rprint(f"[red]No se pudo usar la IA.[/red] {exc}")
        raise typer.Exit(1)
    rprint(f"[green]IA OK ({settings.ai_provider}):[/green] la clave anda y hay saldo. Clasifica con {settings.classify_model} y escribe el plan con {settings.synth_model}.")
    from .ai.creative import check_gemini, enabled

    if settings.gemini_api_key:
        problem = check_gemini()
        if problem:
            rprint(f"[red]Gemini no anda:[/red] {problem}. Las ideas las va a escribir {settings.synth_model}.")
        else:
            rprint(f"[green]Gemini OK:[/green] ideas y guiones con {settings.gemini_creative_model}." if enabled()
                   else "[yellow]Gemini anda pero CREATIVE_PROVIDER no es gemini.[/yellow]")
    else:
        rprint(f"Ideas y guiones: {settings.synth_model} (para usar Gemini, agregá GEMINI_API_KEY en .env).")


@app.command("fill-ig")
def fill_ig(write: bool = typer.Option(False, "--write", help="Guardar los usuarios encontrados en competitors.yaml")) -> None:
    """Busca el Instagram de cada marca en su web (el enlace del encabezado o pie de página)."""
    from .collectors.ig_organic import handle_from_site
    from .config import CONFIG_DIR, load_competitors
    from .discovery import set_fields

    for c in load_competitors():
        if c.instagram:
            rprint(f"{c.name}: @{c.instagram} (ya cargado)")
            continue
        site = c.website or c.google_domain or next(iter(c.google_domains.values()), "")
        h = handle_from_site(site) if site else ""
        rprint(f"{c.name}: {'@' + h if h else '[yellow]no encontrado[/yellow]'}{'' if site else ' (sin web cargada)'}")
        if h and write:
            set_fields(CONFIG_DIR / "competitors.yaml", c.slug, {"instagram": h})
    if not write:
        rprint("\nRevisalos y corré [bold]radar fill-ig --write[/bold] para guardarlos. Los que falten: radar set <marca> --instagram usuario")


@app.command("check-ig")
def check_ig(username: Optional[str] = typer.Argument(None, help="Cuenta pública a probar (por defecto, la del cliente)")) -> None:
    """Prueba el acceso a Instagram orgánico (Business Discovery) con el token de Meta."""
    from .collectors.ig_organic import InstagramOrganic
    from .config import load_competitors

    own = next((c for c in load_competitors() if c.is_own), None)
    username = username or (own.instagram if own else "")
    if not username:
        raise typer.BadParameter("Indicá una cuenta: radar check-ig usuario")
    try:
        prof = InstagramOrganic().profile(username, max_pages=1)
    except Exception as exc:
        rprint(f"[red]No se pudo leer Instagram:[/red] {exc}")
        try:  # qué permisos tiene el token, para saber qué falta
            ig = InstagramOrganic()
            granted = {p["permission"] for p in ig._get("me/permissions").get("data", []) if p.get("status") == "granted"}
            need = ["instagram_basic", "pages_show_list", "pages_read_engagement"]
            missing = [p for p in need if p not in granted]
            rprint(f"Permisos del token: {', '.join(sorted(granted)) or 'ninguno'}")
            if missing:
                rprint(f"[yellow]Le faltan:[/yellow] {', '.join(missing)}. Generá un token nuevo con esos permisos (además de ads_read).")
            else:
                pages = ig._get("me/accounts", fields="name,instagram_business_account{username}", limit=100).get("data", [])
                linked = [f"{p['name']} → @{p['instagram_business_account']['username']}" for p in pages if p.get("instagram_business_account")]
                rprint("Páginas con Instagram vinculado: " + (", ".join(linked) if linked else "[yellow]ninguna[/yellow] (vinculá una cuenta de Instagram profesional a una página que administres)"))
        except Exception as exc2:
            rprint(f"(No se pudieron leer los permisos: {exc2})")
        raise typer.Exit(1)
    rprint(f"[green]Instagram OK:[/green] @{prof.get('username')} · {prof.get('followers_count')} seguidores · {len(prof['posts'])} publicaciones leídas")


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
