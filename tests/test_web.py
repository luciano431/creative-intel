"""Web interna: listado de clientes, alta, tareas en segundo plano y edición de la configuración (sin red)."""
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from creative_intel import web
from creative_intel.config import ROOT


@pytest.fixture
def site(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    for f in ("competitors.yaml", "taxonomy.yaml", "brand.yaml"):
        shutil.copy(ROOT / "config" / f, tmp_path / "config" / f)
    shutil.copytree(ROOT / "config" / "assets", tmp_path / "config" / "assets")
    rep = tmp_path / "reports" / "2026-09"
    rep.mkdir(parents=True)
    (rep / "report.html").write_text("<h1>informe</h1>", encoding="utf-8")
    monkeypatch.setattr("creative_intel.clients.ROOT", tmp_path)
    app = web.create_app(tmp_path)
    return TestClient(app), app, tmp_path


def test_home_lists_default_client_and_reports(site):
    client, _, _ = site
    r = client.get("/")
    assert r.status_code == 200 and "miSeguroOk" in r.text and "2026-09" in r.text
    assert client.get("/reports/2026-09/report.html").text == "<h1>informe</h1>"
    page = client.get("/c/miseguro")
    assert page.status_code == 200 and "Generali" in page.text and "Buscar los IDs que faltan" in page.text


def test_create_client_and_validate_config(site):
    client, _, root = site
    r = client.post("/api/clientes", json={"name": "Banco X", "slug": "banco-x", "industry": "Banca", "market": "Uruguay", "country": "UY"})
    assert r.status_code == 200 and r.json()["url"] == "/c/banco-x"
    assert (root / "config" / "clients" / "banco-x" / "competitors.yaml").exists()
    assert client.post("/api/clientes", json={"name": "Otro", "slug": "Mal Slug"}).status_code == 400
    assert "Banco X" in client.get("/").text
    bad = client.post("/api/c/banco-x/config", json={"text": "esto: [no es"})
    assert bad.status_code == 400
    text = (root / "config" / "clients" / "banco-x" / "competitors.yaml").read_text(encoding="utf-8").replace("Competidor 1", "Banco Uno")
    assert client.post("/api/c/banco-x/config", json={"text": text}).status_code == 200
    assert "Banco Uno" in client.get("/c/banco-x").text


def test_jobs_run_commands_with_client_env(site, monkeypatch):
    client, app, root = site
    seen = []

    def fake_run(cmd, cwd, env, stdout, stderr):
        seen.append((cmd[3:], env.get("RADAR_CLIENT")))
        stdout.write("ok\n")
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(web.subprocess, "run", fake_run)
    client.post("/api/clientes", json={"name": "Banco X", "slug": "banco-x"})
    r = client.post("/api/c/banco-x/tareas", json={"kind": "informe", "period": "2026-10", "ai": False})
    assert r.status_code == 200
    jid = r.json()["id"]
    for _ in range(50):
        if client.get(f"/api/tareas/{jid}").json()["status"] == "listo":
            break
        time.sleep(0.05)
    job = client.get(f"/api/tareas/{jid}").json()
    assert job["status"] == "listo" and job["result_url"] == "/reports/banco-x/2026-10/report.html"
    assert seen == [(["init-db"], "banco-x"), (["run-cycle", "--period", "2026-10", "--no-notify", "--mock-ai"], "banco-x")]
    assert client.post("/api/c/banco-x/tareas", json={"kind": "informe", "period": "octubre"}).status_code == 400
    d = client.post("/api/c/miseguro/tareas", json={"kind": "competidores", "keywords": "seguros", "countries": "ES, AR", "tier": "global"})
    assert d.status_code == 200
    for _ in range(50):
        if client.get(f"/api/tareas/{d.json()['id']}").json()["status"] == "listo":
            break
        time.sleep(0.05)
    cmd, env = seen[-1]
    assert env is None and cmd[:3] == ["discover", "--keywords", "seguros"] and cmd[-4:] == ["--country", "ES", "--country", "AR"]


def test_password_protects_pages_reports_and_api(tmp_path, monkeypatch):
    import base64

    (tmp_path / "config").mkdir()
    for f in ("competitors.yaml", "taxonomy.yaml", "brand.yaml"):
        shutil.copy(ROOT / "config" / f, tmp_path / "config" / f)
    shutil.copytree(ROOT / "config" / "assets", tmp_path / "config" / "assets")
    monkeypatch.setattr("creative_intel.clients.ROOT", tmp_path)
    client = TestClient(web.create_app(tmp_path, password="clave-secreta-123"))
    assert client.get("/").status_code == 401
    assert client.get("/reports/x.html").status_code == 401
    assert client.post("/api/clientes", json={"name": "X", "slug": "x-x"}).status_code == 401
    bad = {"Authorization": "Basic " + base64.b64encode(b"ranker:otra").decode()}
    assert client.get("/", headers=bad).status_code == 401
    good = {"Authorization": "Basic " + base64.b64encode("cualquiera:clave-secreta-123".encode()).decode()}
    assert client.get("/", headers=good).status_code == 200


def test_event_task_validates_and_starts(site, monkeypatch):
    client, app, _ = site
    started = {}
    monkeypatch.setattr(app.state.jobs, "start", lambda c, kind, title, commands, result_url="": started.update(kind=kind, cmd=commands[-1], url=result_url) or type("J", (), {"as_dict": lambda self: {"ok": 1}})())
    assert "Guías de fechas especiales" in client.get("/c/miseguro").text
    assert client.post("/api/c/miseguro/tareas", json={"kind": "evento", "event": "nada"}).status_code == 400
    r = client.post("/api/c/miseguro/tareas", json={"kind": "evento", "event": "cyberlunes", "edition": "2025-06"})
    assert r.status_code == 200 and started["cmd"][-4:] == ["cyberlunes", "--edition", "2025-06", "--mock-ai"]
    assert started["url"].endswith("/eventos/cyberlunes-2025-06/report.html")
    r = client.post("/api/c/miseguro/tareas", json={"kind": "evento", "event": "cyberlunes", "edition": "vivo", "ai": True})
    assert r.status_code == 200 and "--live" in started["cmd"] and "--mock-ai" not in started["cmd"]


def test_events_page_and_multi_client_job(site, monkeypatch):
    client, app, root = site
    shutil.copy(ROOT / "config" / "events.yaml", root / "config" / "events.yaml")
    page = client.get("/eventos")
    assert page.status_code == 200 and "Black Friday" in page.text and "Día de la Madre" in page.text and "miSeguroOk" in page.text
    started = []
    monkeypatch.setattr(app.state.jobs, "start", lambda c, kind, title, cmds, result_url="", links=None:
                        started.append((c, title, cmds, links)) or web.Job("j1", web.GLOBAL, kind, title, cmds, {}, root / "x.log", links=links))
    r = client.post("/api/eventos", json={"event": "black-friday", "edition": "2025", "clients": ["miseguro"], "ai": False})
    assert r.status_code == 200
    c, title, cmds, links = started[0]
    assert c is None and "Black Friday (2025)" in title
    assert cmds[0][3:] == ["all", "evento:black-friday", "--only", "miseguro", "--edition", "2025", "--previa", "10", "--mock-ai"]
    assert links[0]["url"].endswith("/eventos/black-friday-2025/report.html")
    assert client.post("/api/eventos", json={"event": "black-friday", "edition": "2025", "clients": []}).status_code == 400
    assert client.post("/api/eventos", json={"event": "nada", "clients": ["miseguro"]}).status_code == 400


def test_delete_event_guide(site):
    client, _, root = site
    d = root / "reports" / "eventos" / "cyberlunes-2026-06"
    d.mkdir(parents=True)
    (d / "report.html").write_text("x", encoding="utf-8")
    assert client.post("/api/c/miseguro/borrar-guia", json={"name": "../../x"}).status_code == 400
    assert client.post("/api/c/miseguro/borrar-guia", json={"name": "cyberlunes-2026-06"}).status_code == 200
    assert not d.exists()


def test_reports_are_not_cached_and_links_change_with_each_version(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from creative_intel.clients import list_clients
    from creative_intel.web import create_app

    rep = tmp_path / "reports" / "eventos" / "cyberlunes-2026-06"
    rep.mkdir(parents=True)
    (rep / "report.html").write_text("<html>nueva</html>")
    import shutil

    shutil.copytree(__import__("creative_intel.config", fromlist=["ROOT"]).ROOT / "config", tmp_path / "config")
    monkeypatch.setattr("creative_intel.clients.ROOT", tmp_path)
    client = TestClient(create_app(root=tmp_path, password=""))
    r = client.get("/reports/eventos/cyberlunes-2026-06/report.html")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    url = list_clients(tmp_path)[0].event_reports()[0]["url"]
    assert "?v=" in url
