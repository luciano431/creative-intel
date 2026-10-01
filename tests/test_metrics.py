from creative_intel.analysis.metrics import build_metrics, distribution, month_over_month, ours_vs_market, weighted_distribution, white_space
from creative_intel.analysis.concepts import group_concepts


def _item(i, competitor="A", tier="local", platform="meta", fmt="video", days=10, family="proteccion", angle="robo", hook="pregunta", product="celular"):
    return {
        "id": i, "competitor": competitor, "competitor_slug": competitor.lower(), "tier": tier, "platform": platform,
        "format": fmt, "placements": ["feed"], "days_active": days, "status": "active", "first_seen": "2026-08-01",
        "last_seen": "2026-09-01", "engagement": {}, "thumbnail_path": "", "snapshot_url": "", "headline": "h", "body_text": "b",
        "cls": {"angle_family": family, "primary_angle": angle, "hook_type": hook, "product": product, "funnel_stage": "mofu", "creative_style": "ugc", "cta_type": "cotizar", "pain_point": "robo"},
    }


def test_distribution_sorted_with_pct():
    items = [_item(1, family="proteccion"), _item(2, family="proteccion"), _item(3, family="oferta")]
    d = distribution(items, lambda it: it["cls"]["angle_family"])
    assert d[0] == {"value": "proteccion", "count": 2, "pct": 66.7}
    assert d[1]["value"] == "oferta"


def test_white_space_includes_absent_values():
    d = [{"value": "proteccion", "pct": 90.0}, {"value": "oferta", "pct": 10.0}]
    ws = white_space(d, ["proteccion", "oferta", "humor_family"], threshold_pct=20)
    assert [w["value"] for w in ws] == ["humor_family", "oferta"]


def test_month_over_month_deltas():
    cur = [{"value": "proteccion", "pct": 50.0}, {"value": "oferta", "pct": 50.0}]
    prev = [{"value": "proteccion", "pct": 70.0}, {"value": "familia", "pct": 30.0}]
    rows = {r["value"]: r for r in month_over_month(cur, prev)}
    assert rows["proteccion"]["delta"] == -20.0
    assert rows["oferta"]["delta"] == 50.0 and rows["oferta"]["prev_pct"] == 0.0
    assert rows["familia"]["delta"] == -30.0


def test_build_metrics_separates_own_from_market(taxonomy):
    items = [
        _item(1, "SURA", "local", family="proteccion"),
        _item(2, "SURA", "local", family="proteccion", hook="problema"),
        _item(3, "AXA", "global", family="lifestyle", angle="viajes"),
        _item(4, "miSeguro", "own", family="economico", angle="precio"),
    ]
    m = build_metrics(items, taxonomy, previous_items=[_item(9, "SURA", family="oferta", angle="descuento")], period="2026-09")
    assert m["totals"]["creatives"] == 3 and m["totals"]["own_creatives"] == 1
    assert m["by_tier"]["local"]["creatives"] == 2 and m["by_tier"]["global"]["creatives"] == 1
    assert m["angle_matrix"]["SURA"]["proteccion"] == 2
    assert m["mom"]["has_previous"] is True
    assert m["ours_vs_market"]["has_own"] is True
    gaps = {g["value"]: g for g in m["ours_vs_market"]["angle_family"]["gaps"]}
    assert gaps["economico"]["ours_pct"] == 100.0 and gaps["economico"]["market_pct"] == 0.0
    assert "proteccion" not in [w["value"] for w in m["white_space"]["angle_families"]]
    assert "humor" in [w["value"] for w in m["white_space"]["hooks"]]


def test_concepts_group_variants():
    items = [_item(1, "SURA"), _item(2, "SURA", fmt="imagen"), _item(3, "SURA", hook="testimonial"), _item(4, "AXA")]
    concepts = group_concepts(items)
    assert concepts[0]["variants"] == 2 and concepts[0]["formats"] == ["imagen", "video"]
    assert len(concepts) == 3


def test_weighted_distribution_gives_each_competitor_equal_weight():
    items = [_item(i, "Grande", family="oferta") for i in range(10)] + [_item(99, "Chico", family="proteccion")]
    raw = {d["value"]: d["pct"] for d in distribution(items, lambda it: it["cls"]["angle_family"])}
    weighted = {d["value"]: d for d in weighted_distribution(items, lambda it: it["cls"]["angle_family"])}
    assert raw["oferta"] == 90.9
    assert weighted["oferta"]["pct"] == 50.0 and weighted["proteccion"]["pct"] == 50.0
    assert weighted["oferta"]["count"] == 1  # un competidor lo usa


def test_clean_headline_and_google_duration_excluded():
    from creative_intel.analysis.metrics import clean_headline, longevity

    assert clean_headline("0:00 / 0:26") == "" and clean_headline("Ir al perfil de Instagram") == ""
    assert clean_headline("Seguro a tu medida") == "Seguro a tu medida"
    items = [
        {"id": 1, "platform": "meta", "days_active": 40, "raw": {}},
        {"id": 2, "platform": "google", "days_active": 0, "raw": {}},
        {"id": 3, "platform": "google", "days_active": 10, "raw": {"first_seen_known": True}},
    ]
    lon = longevity(items)
    assert lon["dated"] == 2 and lon["median_days"] == 25
    assert longevity([items[1]])["median_days"] is None


def test_promo_detection_counts_secondary_promos_but_not_free_quotes():
    from creative_intel.analysis.metrics import promo_of, promo_summary

    ads = [
        {"cls": {"offer": "20% off el primer año", "angle_family": "emocional"}, "headline": "", "body_text": ""},
        {"cls": {"offer": None}, "headline": "Protegé tu auto", "body_text": "Pagalo en 6 cuotas sin recargo. Cotizá hoy."},
        {"cls": {"offer": "null"}, "headline": "Cotizá gratis en 2 minutos", "body_text": "Seguro de hogar."},
        {"cls": {}, "headline": "1 mes gratis", "body_text": ""},
    ]
    assert promo_of(ads[0]) == "20% off el primer año"
    assert promo_of(ads[1]) == "Pagalo en 6 cuotas sin recargo"
    assert promo_of(ads[2]) is None, "«cotizá gratis» no es una promo"
    s = promo_summary(ads)
    assert (s["ads"], s["total"], s["pct"]) == (3, 4, 75.0)
    assert len(s["examples"]) == 3


def test_hundred_percent_online_is_not_a_promo():
    from creative_intel.analysis.metrics import promo_of

    assert promo_of({"cls": {}, "headline": "Contratá 100% online", "body_text": ""}) is None
    assert promo_of({"cls": {}, "headline": "15% de descuento en tu seguro", "body_text": ""}) == "15% de descuento en tu seguro"


def test_heuristic_offer_is_rechecked_from_text():
    from creative_intel.analysis.metrics import promo_of

    old_rule = {"offer": "100% online", "summary": "Anuncio de auto con ángulo x y hook y (clasificación heurística)."}
    assert promo_of({"cls": old_rule, "headline": "Contratá 100% online", "body_text": ""}) is None
