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
    assert "Executive summary" in html and "Mercado vs miSeguro" in html and "Mes vs mes anterior" in html
    assert "SURA Uruguay" in html
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## 08 — Plan de acción" in md
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
