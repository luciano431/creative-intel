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
    monkeypatch.setattr("creative_intel.meta_token.token_problem", lambda *a, **k: None)
    import creative_intel.ai.event as aiev

    real_mock = aiev.mock_event_plan
    idea = {"title": "Tu celu cubierto", "format": "video", "hook_type": "demo", "angle": "proteccion", "hook_line": "Se cae el celu",
            "script": ["0–3 s · Se ve: un celular cae"], "copy_draft": "Cubrilo hoy", "cta": "Cotizá", "visual_direction": "Casa",
            "evidence": "El anuncio de 12 cuotas salió 1 día antes", "reading": "Las cuotas se usan para cerrar",
            "hypothesis": "Creemos que mostrar la caída baja el costo por lead", "test": "2 versiones, 7 días, corta si el CPL sube 20%"}
    monkeypatch.setattr(aiev, "mock_event_plan", lambda *a, **k: {**real_mock(*a, **k), "ideas": [idea]})
    from creative_intel.collectors.ig_organic import summarize

    posts = [{"id": "1", "caption": "CyberLunes: 20% en tu seguro", "like_count": 90, "comments_count": 10, "timestamp": "2025-06-02T12:00:00+0000"},
             {"id": "2", "caption": "Día del padre", "like_count": 10, "comments_count": 0, "timestamp": "2025-05-20T12:00:00+0000"},
             {"id": "3", "caption": "Otra cosa", "like_count": 20, "comments_count": 0, "timestamp": "2025-05-25T12:00:00+0000"}]
    org = {"rival": {"brand": "Rival", "own": False, "raw": [],
                     **summarize({"username": "rival", "followers_count": 1000, "posts": posts}, ["cyberlunes"],
                                 event_days=(dt.date(2025, 6, 2), dt.date(2025, 6, 4)))}}
    assert org["rival"]["event"]["posts"] == 1 and org["rival"]["event"]["vs_period_pct"] == 131
    monkeypatch.setattr(evmod, "_organic", lambda *a, **k: org)
    monkeypatch.setattr(evmod, "_own_paid_summary", lambda own, ed, errors: {
        "spend": 300.0, "currency": "USD", "by_type": {"compras": {"total": 10, "cost": 30.0}}, "top": [{"ad_name": "Bici 2024", "spend": 100, "results": 5, "result_type": "compras", "cost_per_result": 20}]})
    old_ed = EventEdition(label="2024-11", date_from=dt.date(2024, 10, 14), date_to=dt.date(2024, 11, 12), start=dt.date(2024, 11, 4), end=dt.date(2024, 11, 6))
    with session_scope(url) as s:
        out = evmod.run_event(s, ev, ev.edition(), mock_ai=True, out_dir=tmp_path / "rep", compare=[old_ed], previa=0)
    html = (tmp_path / "rep" / "report.html").read_text(encoding="utf-8")
    assert out["ads"] == 2 and "Guía de CyberLunes" in html and "Tienda X" in html and "1 a 7 días antes" in html
    assert "El evento en sí: 2025-06-02 al 2025-06-04" in html and "Rival: lo que publicó para el evento" in html
    # solo los días del evento: sin la sección del período ni la comparación contra el promedio del período
    assert "Instagram en todo el período" not in html and "Contra su promedio" not in html
    assert "contra sí mismo: cada edición" in html and "Nuestros anuncios, año contra año" in html and "Bici 2024" in html and "2024-11" in html and "US$ 300" in html
    assert "Con qué datos se armó esta guía" in html and "Creemos que mostrar la caída" in html and "Cómo se prueba" in html


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

    days = [{"date": f"2026-05-{d:02d}", "spend": 10.0, "impressions": 100, "clicks": 5, "by_type": {"compras": r, "conversaciones": 3}}
            for d, r in ((20, 1), (21, 1), (25, 6), (26, 4), (28, 2))]
    out = own_daily_summary(days, dt.date(2026, 5, 25), dt.date(2026, 5, 26))
    ph = out["phases"]
    assert out["types"] == ["compras", "conversaciones"]
    assert ph["antes"]["days"] == 2 and ph["durante"]["per_type"]["compras"] == {"total": 10, "per_day": 5.0, "cost": 2.0}
    assert ph["durante"]["per_type"]["conversaciones"]["total"] == 6 and ph["despues"]["spend_per_day"] == 10.0
    rows = own_results([{"id": 1, "tier": "own", "headline": "A", "format": "imagen", "cls": {},
                         "engagement": {"performance": {"spend": 20, "results": 2, "result_type": "compras", "cost_per_result": 10, "ctr": 1.2}}},
                        {"id": 2, "tier": "own", "headline": "B", "format": "video", "cls": {"offer": "20% off"},
                         "engagement": {"performance": {"spend": 30, "results": 6, "result_type": "compras", "cost_per_result": 5, "ctr": 2.0}}}])
    assert [r["headline"] for r in rows] == ["B", "A"]


def test_event_related_drops_always_on_ads():
    from creative_intel.events import event_related

    items = [
        {"id": 1, "platform": "meta", "headline": "Seguro de auto", "body_text": "", "cls": {}, "first_seen": "2025-09-01"},
        {"id": 2, "platform": "meta", "headline": "CyberLunes 30% off", "body_text": "", "cls": {}, "first_seen": "2025-09-01"},
        {"id": 3, "platform": "meta", "headline": "Promo nueva", "body_text": "", "cls": {}, "first_seen": "2026-05-20"},
        {"id": 4, "platform": "google", "headline": "Cotizá", "body_text": "", "cls": {}, "first_seen": "2026-05-20", "raw": {}},
    ]
    keep, dropped = event_related(items, ["cyberlunes"], dt.date(2026, 5, 11))
    assert [i["id"] for i in keep] == [2, 3] and dropped == 2


def test_gemini_writes_every_piece_and_failure_keeps_briefs(monkeypatch):
    from types import SimpleNamespace

    import creative_intel.ai.creative as cr
    from creative_intel.config import EventConfig
    from creative_intel.events import _gemini_pieces

    ev = EventConfig(key="cyberlunes", name="CyberLunes", keywords=["cyberlunes"], countries=["UY"], editions=[])
    brief = {"title": "Bici 25", "moment": "el día", "format": "imagen", "product": "bici", "angle": "oferta", "brief": "Vender bici",
             "evidence": "12 compras", "reading": "simple", "hypothesis": "Creemos que", "test": "7 días"}
    plan = {"summary": [], "offer_recommendation": "x", "avoid": [], "ideas": [brief],
            "calendar": [{"when": "el día", "goal": "lanzar", "format": "imagen", "intent": "anunciar la oferta de bici"}]}
    client = SimpleNamespace(name="miSeguroOk", industry="Seguros", description="", brief=None)
    piece = {"for_what": "Bici 25", "title": "Tu bici, cubierta", "format": "imagen", "hook_type": "oferta", "angle": "oferta",
             "hook_line": "Se ve una bici", "script": ["0–3 s · Se ve: bici"], "copy_draft": "Asegurala hoy", "cta": "Cotizá", "visual_direction": "Calle"}
    monkeypatch.setattr(cr, "write_event_pieces", lambda *a, **k: {"ideas": [piece], "calendar": [{"when": "el día", "message": "Arrancó", "cta": "Entrá"}]})
    out = _gemini_pieces(plan, ev, {}, [], client)
    i = out["ideas"][0]
    assert out["ideas_by"] == "Gemini" and i["title"] == "Tu bici, cubierta" and i["brief_title"] == "Bici 25" and i["evidence"] == "12 compras"
    assert out["calendar"][0]["message"] == "Arrancó" and out["calendar"][0]["intent"] == "anunciar la oferta de bici"

    def boom(*a, **k):
        raise cr.GeminiFailed("sin cupo")

    monkeypatch.setattr(cr, "write_event_pieces", boom)
    out = _gemini_pieces(plan, ev, {}, [], client)
    assert out["creative_error"] == "sin cupo" and out["ideas"] == [brief] and "ideas_by" not in out
