"""Muestra que ve la IA y recuadro de cobertura de datos."""
from creative_intel.ai.synthesize import sample_items, sample_note
from creative_intel.analysis.data_coverage import event_coverage, plan_coverage


def _ad(i, brand, days, first, tier="local", spend=0):
    return {"id": i, "competitor": brand, "tier": tier, "days_active": days, "first_seen": first,
            "engagement": {"performance": {"spend": spend}} if spend else {}}


def test_sample_spreads_brands_and_keeps_new_ads():
    big = [_ad(i, "Grande", 300 - i, f"2026-01-{i % 28 + 1:02d}") for i in range(100)]
    new = _ad(500, "Grande", 1, "2026-10-01")
    small = [_ad(600 + i, "Chica", 10, "2026-09-01") for i in range(3)]
    own = [_ad(900, "Cliente", 5, "2026-09-01", "own", spend=50), _ad(901, "Cliente", 90, "2026-05-01", "own", spend=500)]
    picked = sample_items(big + [new] + small + own, 12)
    ids = [it["id"] for it in picked]
    assert ids[:2] == [901, 900]  # propios primero, por inversión
    assert 500 in ids and all(600 + i in ids for i in range(3))  # el más nuevo y la marca chica entran
    note = sample_note(big + [new] + small + own, 12)
    assert note["total"] == 106 and note["sent"] == 12 and note["by_brand"]["Chica"] == {"total": 3, "sent": 3}
    assert sample_items(small, 10) == small


def test_event_coverage_levels():
    full = {"own_results": [{"spend": 1000}] * 5, "own_daily": {"phases": {"durante": {}}}, "ads": 12, "currency": "$",
            "organic": [{"own": True, "posts": 9}, {"own": False, "posts": 4}, {"own": False, "posts": 2}]}
    c = event_coverage(full)
    assert c["level"] == "alta" and c["rows"][0]["detail"] == "5 anuncios, $ 5.000 invertidos" and not c["missing"]
    thin = {"own_results": [], "ads": 0, "organic": [{"own": True, "posts": 3}, {"own": False, "posts": 0, "source": "perfil_publico"}],
            "collect_errors": []}
    c = event_coverage(thin)
    assert c["level"] == "baja" and c["rows"][0]["detail"] == "sin pauta en esas fechas"
    assert "solo se ven las últimas" in c["rows"][-1]["detail"]
    assert plan_coverage({"own_audit": {"active": 4}, "own_performance": {"total": {"ads": 10}}}, 40, 3)["level"] == "alta"
