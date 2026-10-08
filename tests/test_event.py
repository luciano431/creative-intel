"""Modo evento: análisis de una edición pasada (timing, ofertas, urgencia) sin red ni IA."""
import datetime as dt

from creative_intel.analysis.event import analyze_event, peak_day


def _ad(i, first, text, comp="A", status="inactive", days=3, fmt="imagen"):
    return {"id": i, "competitor": comp, "first_seen": first, "headline": text, "body_text": "", "cls": {}, "status": status,
            "days_active": days, "format": fmt}


def test_timing_offers_and_urgency_are_counted():
    ads = [
        _ad(1, "2025-05-20", "Se viene el CyberLunes: anotate"),
        _ad(2, "2025-05-29", "CyberLunes 30% off en toda la tienda"),
        _ad(3, "2025-06-02", "Solo hoy 50% off + 12 cuotas sin interés", comp="B"),
        _ad(4, "2025-06-06", "Extendimos: envío gratis", comp="B", fmt="video"),
    ]
    m = analyze_event(ads, dt.date(2025, 6, 2), dt.date(2025, 6, 4), own_name="B")
    assert m["timing"]["phases"] == {"anticipo": 1, "previa": 1, "durante": 1, "despues": 1}
    assert m["timing"]["earliest_days_before"] == 13
    assert m["offers"]["pct_max"] == 50 and m["offers"]["cuotas_max"] == 12
    offers = {d["value"]: d["count"] for d in m["offers"]["dist"]}
    assert offers["descuento"] == 2 and offers["cuotas"] == 1 and offers["envio_gratis"] == 1
    urg = {d["value"]: d["count"] for d in m["urgency"]}
    assert urg["solo hoy / por tiempo limitado"] == 1 and urg["fecha del evento"] == 2
    assert m["own_ads"] == 2 and m["official_dates"] is True


def test_without_official_dates_uses_the_peak_day():
    ads = [_ad(1, "2025-11-03", "x"), _ad(2, "2025-11-03", "y"), _ad(3, "2025-10-28", "z")]
    assert peak_day(ads) == dt.date(2025, 11, 3)
    m = analyze_event(ads, None, None)
    assert m["event_start"] == "2025-11-03" and m["official_dates"] is False


def test_event_pipeline_end_to_end_without_network_or_ai(tmp_path, monkeypatch):
    from creative_intel import events as evmod
    from creative_intel.collectors.base import RawCreative
    from creative_intel.config import EventConfig, EventEdition, load_competitors
    from creative_intel.db import init_db, session_scope

    url = f"sqlite:///{tmp_path / 'e.db'}"
    init_db(url)
    local = next(c for c in load_competitors() if c.tier == "local")

    class FakeCol:
        def collect_window(self, c, country, a, b, limit):
            if c.slug != local.slug:
                return []
            return [RawCreative(competitor_slug=c.slug, platform="meta", source="meta_web", external_id="w1", headline="CyberLunes 40% off",
                                first_seen=dt.date(2025, 5, 30), last_seen=dt.date(2025, 6, 3), is_active=False, countries=["UY"])]

        def collect_keyword(self, kw, country, a, b, limit, focus=None):
            return [RawCreative(competitor_slug="mkt-tienda-x", platform="meta", source="meta_web", external_id=f"k-{kw}",
                                headline="Solo hoy: seguro de celular en 12 cuotas sin interés", first_seen=dt.date(2025, 6, 2), is_active=False,
                                countries=["UY"], raw={"page_name": "Tienda X"})]

    ev = EventConfig(key="cyberlunes", name="CyberLunes", keywords=["cyberlunes"], countries=["UY"],
                     editions=[EventEdition(label="2025-06", date_from=dt.date(2025, 5, 12), date_to=dt.date(2025, 6, 10),
                                            start=dt.date(2025, 6, 2), end=dt.date(2025, 6, 4))])
    monkeypatch.setattr("creative_intel.collectors.meta_web.MetaAdLibraryWebCollector", FakeCol)
    with session_scope(url) as s:
        out = evmod.run_event(s, ev, ev.edition(), mock_ai=True, out_dir=tmp_path / "rep")
    html = (tmp_path / "rep" / "report.html").read_text(encoding="utf-8")
    assert out["ads"] == 2 and "Guía de CyberLunes" in html and "Tienda X" in html and "1 a 7 días antes" in html


def test_live_edition_uses_next_dates_only_when_close():
    from creative_intel.config import EventConfig
    from creative_intel.events import live_edition

    ev = EventConfig(key="cyberlunes", name="CyberLunes", keywords=["cyberlunes"], countries=["UY"], editions=[],
                     next_start=dt.date(2026, 11, 2), next_end=dt.date(2026, 11, 4))
    ed = live_edition(ev, today=dt.date(2026, 10, 6))
    assert ed.label == "vivo-2026-10" and ed.date_from == dt.date(2026, 8, 7) and ed.start == dt.date(2026, 11, 2)
    old = EventConfig(key="x", name="X", keywords=[], countries=["UY"], editions=[], next_start=dt.date(2025, 1, 1))
    assert live_edition(old, today=dt.date(2026, 10, 6)).start is None


def test_own_daily_summary_splits_before_during_after():
    from creative_intel.analysis.event import own_daily_summary, own_results

    days = [{"date": f"2026-05-{d:02d}", "spend": 10.0, "impressions": 100, "clicks": 5, "results": r, "result_type": "compras"}
            for d, r in ((20, 1), (21, 1), (25, 6), (26, 4), (28, 2))]
    out = own_daily_summary(days, dt.date(2026, 5, 25), dt.date(2026, 5, 26))
    ph = out["phases"]
    assert ph["antes"]["days"] == 2 and ph["durante"]["results"] == 10 and ph["durante"]["cost_per_result"] == 2.0
    assert ph["despues"]["spend_per_day"] == 10.0 and out["result_type"] == "compras"
    rows = own_results([{"id": 1, "tier": "own", "headline": "A", "format": "imagen", "cls": {},
                         "engagement": {"performance": {"spend": 20, "results": 2, "cost_per_result": 10, "ctr": 1.2}}},
                        {"id": 2, "tier": "own", "headline": "B", "format": "video", "cls": {"offer": "20% off"},
                         "engagement": {"performance": {"spend": 30, "results": 6, "cost_per_result": 5, "ctr": 2.0}}}])
    assert [r["headline"] for r in rows] == ["B", "A"]
