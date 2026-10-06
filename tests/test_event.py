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

        def collect_keyword(self, kw, country, a, b, limit):
            return [RawCreative(competitor_slug="mkt-tienda-x", platform="meta", source="meta_web", external_id=f"k-{kw}",
                                headline="Solo hoy: 12 cuotas sin interés", first_seen=dt.date(2025, 6, 2), is_active=False,
                                countries=["UY"], raw={"page_name": "Tienda X"})]

    ev = EventConfig(key="cyberlunes", name="CyberLunes", keywords=["cyberlunes"], countries=["UY"],
                     editions=[EventEdition(label="2025-06", date_from=dt.date(2025, 5, 12), date_to=dt.date(2025, 6, 10),
                                            start=dt.date(2025, 6, 2), end=dt.date(2025, 6, 4))])
    monkeypatch.setattr("creative_intel.collectors.meta_web.MetaAdLibraryWebCollector", FakeCol)
    with session_scope(url) as s:
        out = evmod.run_event(s, ev, ev.edition(), mock_ai=True, out_dir=tmp_path / "rep")
    html = (tmp_path / "rep" / "report.html").read_text(encoding="utf-8")
    assert out["ads"] == 2 and "Guía de CyberLunes" in html and "Tienda X" in html and "1 a 7 días antes" in html
