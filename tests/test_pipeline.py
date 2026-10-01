import datetime as dt
import json

from creative_intel import pipeline
from creative_intel.db import Creative, get_or_create_run
from creative_intel.normalize import upsert_creatives
from creative_intel.review import data_qa_queue, latest_synthesis, review_synthesis, set_qa_status


def test_full_cycle_two_months_with_mock_ai(session, sample_raws, tmp_path):
    # Mes anterior con un subconjunto
    prev_end = dt.date(2026, 8, 31)
    prev = get_or_create_run(session, "2026-08")
    upsert_creatives(session, prev, [r for r in sample_raws if r.first_seen <= prev_end], prev_end)
    pipeline.classify(session, "2026-08", mock=True)
    pipeline.analyze(session, "2026-08")

    # Mes actual completo, vía collect con extra_raws (sin fuentes externas)
    stats = pipeline.collect(session, "2026-09", sources=["manual"], extra_raws=sample_raws, seen_on=dt.date(2026, 9, 25), download_media=False)
    assert stats["new"] + stats["updated"] == len(sample_raws)
    summary = pipeline.run_cycle(session, "2026-09", mock_ai=True, skip_collect=True, out_dir=tmp_path, send_notifications=False)
    assert summary["totals"]["creatives"] == len([r for r in sample_raws if r.competitor_slug != "miseguro"])
    assert summary["totals"]["own_creatives"] == 4

    run = get_or_create_run(session, "2026-09")
    assert run.status == "draft"
    assert run.metrics["mom"]["has_previous"] is True
    assert run.metrics["ours_vs_market"]["has_own"] is True
    assert (tmp_path / "report.html").exists() and (tmp_path / "report.md").exists() and (tmp_path / "basecamp.html").exists()
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "Plan creativo para" in html and "Resumen ejecutivo" in html and "Qué cambió respecto al mes anterior" in html
    assert "Competidor por competidor" in html and "Top 10 locales" in html and "Top 10 globales" in html
    assert "miSeguro vs. mercado" in html and "Plan de acción" in html
    for gone in ("Executive summary", "Competitive landscape", "Anexo", "Ponderado por competidor", "INFERENCIA", "confianza medium"):
        assert gone not in html, gone
    assert "SURA Uruguay" in html
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "— Plan de acción" in md and "Resumen ejecutivo" in md
    json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))

    # Checkpoint 1: la cola de QA existe y se resuelve
    queue = data_qa_queue(session, run)
    assert queue, "la heurística marca low confidence, debe haber cola de QA"
    set_qa_status(session, [queue[0]["id"]], "rejected", "no es de este competidor")
    assert session.get(Creative, queue[0]["id"]).qa_status == "rejected"
    # Los rechazados salen del análisis
    n_before = run.metrics["totals"]["creatives"]
    pipeline.analyze(session, "2026-09")
    assert run.metrics["totals"]["creatives"] == n_before - 1

    # Checkpoint 2: publish bloqueado hasta aprobar
    try:
        pipeline.publish(session, "2026-09")
        assert False, "debe exigir aprobación"
    except PermissionError:
        pass
    synth = review_synthesis(session, run, "approve", "Ana", "ok")
    assert synth.status == "approved" and run.status == "approved"


def test_review_edit_creates_new_approved_version(session, sample_raws, tmp_path):
    run = get_or_create_run(session, "2026-09")
    upsert_creatives(session, run, sample_raws, dt.date(2026, 9, 25))
    pipeline.classify(session, "2026-09", mock=True)
    pipeline.synthesize_run(session, "2026-09", mock=True)
    first = latest_synthesis(session, run)
    edited = dict(first.data)
    edited["executive_summary"] = ["Editado por humano 1", "2", "3"]
    second = review_synthesis(session, run, "edit", "Ana", "ajusté el summary", edited=edited)
    assert second.id != first.id and second.status == "approved" and second.model.endswith("+human")
    assert latest_synthesis(session, run).data["executive_summary"][0] == "Editado por humano 1"
    paths = pipeline.build_report(session, "2026-09", tmp_path)
    assert "Editado por humano 1" in paths["report.html"].read_text(encoding="utf-8")



def test_purge_removes_classifications_and_orphans(session, sample_raws):
    from sqlalchemy import func, select

    from creative_intel.db import Classification, Sighting, purge_creatives

    run = get_or_create_run(session, "2026-09")
    upsert_creatives(session, run, sample_raws, dt.date(2026, 9, 25))
    pipeline.classify(session, "2026-09", mock=True)
    n = session.scalar(select(func.count()).select_from(Creative))
    slug = sample_raws[0].competitor_slug
    out = purge_creatives(session, slugs=[slug])
    assert out["creatives"] > 0
    assert session.scalar(select(func.count()).select_from(Creative)) == n - out["creatives"]
    alive = select(Creative.id)
    assert session.scalar(select(func.count()).select_from(Classification).where(Classification.creative_id.not_in(alive))) == 0
    assert session.scalar(select(func.count()).select_from(Sighting).where(Sighting.creative_id.not_in(alive))) == 0
