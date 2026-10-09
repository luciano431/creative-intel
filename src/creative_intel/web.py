"""Web interna del Radar: elegir cliente, generar el informe del mes y verlo, dar de alta clientes y buscar IDs.

    radar web            # abre http://127.0.0.1:8080

Cada tarea corre el mismo comando que en la terminal (`radar run-cycle`, `radar fill-ids`, `radar discover`)
en un proceso aparte con RADAR_CLIENT del cliente elegido, y la página muestra su avance.
"""
from __future__ import annotations

import base64
import datetime as dt
import os
import secrets
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .clients import PERIOD_RE, ClientEntry, get_client, list_clients, scaffold_client
from .config import ROOT, load_brand, load_competitors, load_events, settings

TEMPLATES = Path(__file__).parent / "web_templates"
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
COUNTRY_RE = re.compile(r"^[A-Za-z]{2,3}$")


# ----------------------------------------------------------------------------- tareas en segundo plano


@dataclass
class Job:
    id: str
    client: str
    kind: str
    title: str
    commands: list[list[str]]
    env: dict[str, str]
    log_path: Path
    status: str = "pendiente"  # pendiente | corriendo | listo | error
    started: float = field(default_factory=time.time)
    finished: float | None = None
    result_url: str = ""
    links: list[dict[str, str]] = field(default_factory=list)  # tareas de varios clientes: un informe por cliente

    def tail(self, lines: int = 40) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""
        return "\n".join(text.splitlines()[-lines:])

    def as_dict(self) -> dict[str, Any]:
        try:
            full = self.log_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            full = ""
        return {"id": self.id, "client": self.client, "kind": self.kind, "title": self.title, "status": self.status,
                "started": self.started, "finished": self.finished, "elapsed": int((self.finished or time.time()) - self.started),
                "log": self.tail(), "result_url": self.result_url, "links": self.links,
                "progress": progress_of(full, len(self.links) or 1)}


STAGES = ["Buscando", "Clasificando", "Analizando", "Escribiendo", "Armando"]
SOURCES = (("[meta_web]", "Meta"), ("[google_web]", "Google"), ("[instagram]", "Instagram"), ("[meta_insights]", "resultados propios"))


def progress_of(text: str, total: int) -> dict[str, Any]:
    """En qué anda una tarea, leído de su registro: cliente actual, paso y porcentaje aproximado."""
    heads = list(re.finditer(r"══ (.+?) ══", text))
    seg = text[heads[-1].end():] if heads else text
    stages = re.findall(r"\[etapa\] (.+)", seg)
    step = stages[-1].strip() if stages else "Preparando"
    if step.startswith("Buscando"):
        last = max(((seg.rfind(tag), name) for tag, name in SOURCES), default=(-1, ""))
        if last[0] >= 0:
            step += f" en {last[1]}"
    k = next((i + 1 for i, w in enumerate(STAGES) if step.startswith(w)), 0)
    i = max(1, len(heads))
    pct = round(100 * ((i - 1) + k / (len(STAGES) + 1)) / max(total, 1))
    return {"client": heads[-1].group(1) if heads else "", "i": i, "total": total, "step": step, "pct": min(pct, 99)}


GLOBAL = "_todos"  # tareas que recorren varios clientes (guías de evento)


class JobManager:
    def __init__(self, log_dir: Path):
        self.jobs: dict[str, Job] = {}
        self.log_dir = log_dir
        self.lock = threading.Lock()

    def running_for(self, client: str) -> Job | None:
        return next((j for j in self.jobs.values() if j.client == client and j.status in ("pendiente", "corriendo")), None)

    def recent_for(self, client: str, n: int = 5) -> list[Job]:
        return sorted((j for j in self.jobs.values() if j.client == client), key=lambda j: -j.started)[:n]

    def start(self, client: ClientEntry | None, kind: str, title: str, commands: list[list[str]], result_url: str = "",
              links: list[dict[str, str]] | None = None) -> Job:
        """client=None: tarea de varios clientes (cada comando elige su cliente)."""
        key = client.key if client else GLOBAL
        with self.lock:
            if self.running_for(key):
                raise HTTPException(409, "Ya hay una tarea corriendo para este cliente; esperá a que termine."
                                    if client else "Ya hay una guía de evento generándose; esperá a que termine.")
            self.log_dir.mkdir(parents=True, exist_ok=True)
            jid = uuid.uuid4().hex[:10]
            env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "160", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
            env.pop("RADAR_CLIENT", None)
            if client and client.env:
                env["RADAR_CLIENT"] = client.env
            job = Job(jid, key, kind, title, commands, env, self.log_dir / f"{jid}.log", result_url=result_url, links=links or [])
            self.jobs[jid] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        job.status = "corriendo"
        ok = True
        with job.log_path.open("w", encoding="utf-8") as log:
            for cmd in job.commands:
                log.write(f"$ radar {' '.join(cmd[3:])}\n")
                log.flush()
                try:
                    rc = subprocess.run(cmd, cwd=ROOT, env=job.env, stdout=log, stderr=subprocess.STDOUT).returncode
                except Exception as exc:  # pragma: no cover
                    log.write(f"Error: {exc}\n")
                    rc = 1
                if rc != 0:
                    log.write(f"\nEl paso terminó con error (código {rc}).\n")
                    ok = False
                    break
        job.status = "listo" if ok else "error"
        job.finished = time.time()


def _radar(*args: str) -> list[str]:
    return [sys.executable, "-m", "creative_intel.cli", *args]


# ----------------------------------------------------------------------------- app


def _password_ok(header: str, password: str) -> bool:
    if not header.lower().startswith("basic "):
        return False
    try:
        given = base64.b64decode(header[6:]).decode("utf-8").partition(":")[2]
    except Exception:
        return False
    return secrets.compare_digest(given.encode(), password.encode())


def create_app(root: Path | None = None, password: str | None = None) -> FastAPI:
    root = root or ROOT
    app = FastAPI(title="Radar creativo de Ranker", docs_url=None, redoc_url=None)
    password = os.getenv("WEB_PASSWORD", "") if password is None else password

    if password:
        # Todo (páginas, informes y tareas) pide la contraseña; el usuario puede ser cualquiera.
        @app.middleware("http")
        async def require_password(request: Request, call_next):
            if not _password_ok(request.headers.get("authorization", ""), password):
                return Response("Se necesita la contraseña del Radar.", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="Radar creativo", charset="UTF-8"'})
            return await call_next(request)
    jobs = JobManager(root / "data" / "web_jobs")
    app.state.jobs = jobs
    (root / "reports").mkdir(exist_ok=True)
    app.mount("/reports", StaticFiles(directory=root / "reports", html=True), name="reports")

    def render(name: str, **ctx: Any) -> HTMLResponse:
        brand = load_brand()
        return HTMLResponse(_env.get_template(name).render(brand=brand, C={**brand.colors}, **ctx))

    def client_or_404(key: str) -> ClientEntry:
        c = get_client(key, root)
        if not c:
            raise HTTPException(404, "Cliente no encontrado")
        return c

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        clients = list_clients(root)
        return render("index.html.j2", clients=clients, running={c.key: jobs.running_for(c.key) for c in clients})

    @app.get("/eventos", response_class=HTMLResponse)
    def events_page() -> HTMLResponse:
        from .events import live_edition

        events = load_events(root / "config")
        today = dt.date.today()
        rows = []
        for ev in events.values():
            nxt = ev.next_start
            rows.append({"key": ev.key, "name": ev.name, "next": nxt.isoformat() if nxt else "",
                         "days": (nxt - today).days if nxt else None, "live": live_edition(ev, today).label,
                         "editions": [{"label": e.label, "start": e.start.isoformat() if e.start else "",
                                       "end": e.end.isoformat() if e.end else ""} for e in reversed(ev.editions)]})
        rows.sort(key=lambda r: (r["days"] is None or r["days"] < 0, r["days"] if r["days"] is not None else 9999))
        clients = list_clients(root)
        reports = {c.key: c.event_reports() for c in clients}
        return render("events.html.j2", events=rows, clients=clients, reports=reports,
                      jobs=[j.as_dict() for j in jobs.recent_for(GLOBAL)], running=jobs.running_for(GLOBAL))

    @app.post("/api/eventos")
    async def start_events(request: Request) -> JSONResponse:
        from .events import live_edition

        body = await request.json()
        events = load_events(root / "config")
        key_ev = str(body.get("event") or "")
        if key_ev not in events:
            raise HTTPException(400, "Elegí un evento")
        ev = events[key_ev]
        edition = str(body.get("edition") or "vivo")
        if edition == "vivo":
            label, flags = live_edition(ev).label, ["--live"]
        elif edition in {e.label for e in ev.editions}:
            label, flags = edition, ["--edition", edition]
        else:
            raise HTTPException(400, "Edición desconocida")
        all_clients = {c.key: c for c in list_clients(root)}
        chosen = [k for k in body.get("clients") or [] if k in all_clients]
        if not chosen:
            raise HTTPException(400, "Elegí al menos un cliente")
        ai = bool(body.get("ai"))
        cmd = _radar("all", f"evento:{key_ev}", *[x for k in chosen for x in ("--only", k)], *flags, *([] if ai else ["--mock-ai"]))
        links = [{"name": all_clients[k].name,
                  "url": all_clients[k].report_url("x").rsplit("/x/", 1)[0] + f"/eventos/{key_ev}-{label}/report.html"} for k in chosen]
        title = f"{ev.name} ({'en curso' if edition == 'vivo' else label}) · {len(chosen)} cliente{'s' if len(chosen) > 1 else ''}" \
                + (" · con IA" if ai else " · sin IA")
        job = jobs.start(None, "evento", title, [cmd], links=links)
        return JSONResponse(job.as_dict())

    @app.get("/nuevo", response_class=HTMLResponse)
    def new_form() -> HTMLResponse:
        return render("new.html.j2")

    @app.get("/c/{key}", response_class=HTMLResponse)
    def client_page(key: str) -> HTMLResponse:
        c = client_or_404(key)
        from .collectors.google_web import domain_of

        today = dt.date.today()
        return render(
            "client.html.j2", c=c, domain_of=domain_of, jobs=[j.as_dict() for j in jobs.recent_for(key)],
            running=jobs.running_for(key), default_period=today.strftime("%Y-%m"),
            has_api_key=bool(os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or _dotenv_has_key(root)),
            ai_name="OpenAI" if settings.ai_provider == "openai" else "Claude",
            config_text=c.config_path.read_text(encoding="utf-8"),
            events=load_events(c.config_path.parent),
        )

    @app.post("/api/clientes")
    async def create_client(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            dest = scaffold_client(str(body.get("slug", "")).strip().lower(), str(body.get("name", "")), str(body.get("industry", "")),
                                   str(body.get("country", "UY")), str(body.get("market", "")), clients_dir=root / "config" / "clients")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse({"ok": True, "url": f"/c/{dest.name}"})

    @app.post("/api/c/{key}/tareas")
    async def start_job(key: str, request: Request) -> JSONResponse:
        c = client_or_404(key)
        body = await request.json()
        kind = body.get("kind")
        if kind == "informe":
            period = str(body.get("period") or "")
            if not PERIOD_RE.match(period):
                raise HTTPException(400, "Período inválido (AAAA-MM)")
            run = ["run-cycle", "--period", period, "--no-notify"]
            if not body.get("ai"):
                run.append("--mock-ai")
            if body.get("skip_collect"):
                run.append("--skip-collect")
            job = jobs.start(c, kind, f"Informe {period}" + (" con IA" if body.get("ai") else " sin IA"),
                             [_radar("init-db"), _radar(*run)], result_url=c.report_url(period))
        elif kind == "evento":
            events = load_events(c.config_path.parent)
            key_ev = str(body.get("event") or "")
            if key_ev not in events:
                raise HTTPException(400, "Evento desconocido")
            args = ["evento", key_ev]
            edition = str(body.get("edition") or "")
            if edition == "vivo":
                from .events import live_edition

                args.append("--live")
                ed = live_edition(events[key_ev])
            elif edition:
                if edition not in {e.label for e in events[key_ev].editions}:
                    raise HTTPException(400, "Edición desconocida")
                args += ["--edition", edition]
            if not body.get("ai"):
                args.append("--mock-ai")
            if edition != "vivo":
                ed = events[key_ev].edition(edition or None)
            job = jobs.start(c, kind, f"Guía de {events[key_ev].name} ({ed.label})" + (" con IA" if body.get("ai") else " sin IA"),
                             [_radar("init-db"), _radar(*args)],
                             result_url=c.report_url("x").rsplit("/x/", 1)[0] + f"/eventos/{key_ev}-{ed.label}/report.html")
        elif kind == "ids":
            job = jobs.start(c, kind, "Completar IDs de Meta y Google", [_radar("init-db"), _radar("fill-ids", "--write")])
        elif kind == "competidores":
            words = str(body.get("keywords") or "").strip()
            countries = [x.strip().upper() for x in re.split(r"[,\s]+", str(body.get("countries") or "")) if x.strip()]
            tier = body.get("tier")
            top = int(body.get("top") or 5)
            if not words or not countries or tier not in ("local", "global") or not all(COUNTRY_RE.match(x) for x in countries):
                raise HTTPException(400, "Faltan palabras clave, países (ej. UY o ES, AR, MX) o el tipo (local/global)")
            args = ["discover", "--keywords", words, "--top", str(max(1, min(top, 10))), "--add", tier]
            for x in countries:
                args += ["--country", x]
            job = jobs.start(c, kind, f"Proponer competidores {tier}es ({', '.join(countries)})", [_radar(*args)])
        else:
            raise HTTPException(400, "Tarea desconocida")
        return JSONResponse(job.as_dict())

    @app.get("/api/tareas/{jid}")
    def job_status(jid: str) -> JSONResponse:
        job = jobs.jobs.get(jid)
        if not job:
            raise HTTPException(404, "Tarea no encontrada")
        return JSONResponse(job.as_dict())

    @app.post("/api/c/{key}/config")
    async def save_config(key: str, request: Request) -> JSONResponse:
        c = client_or_404(key)
        text = str((await request.json()).get("text", ""))
        try:
            data = yaml.safe_load(text) or {}
            if not isinstance(data, dict) or not data.get("countries"):
                raise ValueError("El archivo tiene que tener la sección countries")
        except Exception as exc:
            raise HTTPException(400, f"El archivo no es válido: {exc}") from exc
        tmp = c.config_path.with_suffix(".yaml.tmp")
        tmp.write_text(text, encoding="utf-8")
        try:
            load_competitors(tmp)
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            raise HTTPException(400, f"El archivo no es válido: {exc}") from exc
        shutil.copy(c.config_path, c.config_path.with_suffix(".yaml.bak"))
        tmp.replace(c.config_path)
        return JSONResponse({"ok": True})

    return app


def _dotenv_has_key(root: Path) -> bool:
    env = root / ".env"
    if not env.exists():
        return False
    return any(re.match(r"^\s*(ANTHROPIC|OPENAI)_API_KEY\s*=\s*\S+", line) for line in env.read_text(encoding="utf-8", errors="ignore").splitlines())



