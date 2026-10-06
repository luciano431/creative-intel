"""Señales de lo que funciona: versiones, meses en el top y movimiento entre meses (solo hechos)."""
from creative_intel.analysis.signals import build_signals, movement, signal_list, versions_of


def test_versions_from_each_platform():
    assert versions_of("google", {"variations": 4}) == 4
    assert versions_of("meta", {"card_text": "6 anuncios usan este contenido y texto\nHola"}) == 6
    assert versions_of("meta", {"multiple_versions": True}) == 2
    assert versions_of("meta", {}) == 1


def _it(i, comp="A", platform="meta", **kw):
    return {"id": i, "competitor": comp, "platform": platform, "days_active": kw.pop("days", 10), "versions": kw.pop("versions", 1),
            "months_in_top": kw.pop("months", 1), "headline": f"anuncio {i}", "cls": {}, "format": "imagen", **kw}


def test_movement_new_kept_dropped_only_where_there_was_data():
    prev = [_it(1), _it(2), _it(9, comp="B", platform="google")]
    cur = [_it(2), _it(3), _it(4, comp="C")]
    mov = movement(cur, prev)
    assert mov["by_competitor"]["A"] == {"new": 1, "kept": 1, "dropped": 1, "first_time": 0}
    assert mov["by_competitor"]["B"]["dropped"] == 1
    assert mov["by_competitor"]["C"]["first_time"] == 1
    assert [c["track"] for c in cur] == ["sigue", "nuevo", "primera"]


def test_signals_are_facts_and_rank_by_persistence():
    cur = [_it(1, days=5), _it(2, days=40, versions=6, months=3)]
    out = build_signals(cur, [], [{"competitor": "A", "name": "x", "variants": 3, "first_seen": "2026-09-01", "angle": "a", "product": "p", "hook_type": "h"}],
                        lambda it: True)
    assert out["top"][0]["id"] == 2
    assert out["top"][0]["signals"] == ["40 días al aire", "6 versiones", "3 meses seguidos entre los más vistos"]
    assert out["repeated_ideas"][0]["variants"] == 3
    assert signal_list(_it(3, platform="google", days=0), start_known=False) == []
