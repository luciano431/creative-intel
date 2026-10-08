from creative_intel.config import load_client, load_competitors
from creative_intel.relevance import filter_items, matches


def test_matches_stems_accents_and_exact_words():
    kws = ["parrill", "=cama", "jardin"]
    assert matches("Parrillas de HIERRO", kws)
    assert matches("Todo para tu jardín", kws)
    assert matches("Cama de 2 plazas", kws)
    assert not matches("Cámara de fotos 20% off", kws)
    assert not matches("Taladro percutor Bosch", kws)
    assert matches("lo que sea", [])


def test_filter_only_generalists_and_market():
    items = [
        {"competitor_slug": "sodimac", "tier": "local", "headline": "Taladro Bosch", "body_text": "", "cls": {}},
        {"competitor_slug": "sodimac", "tier": "local", "headline": "Juego de jardín", "body_text": "", "cls": {}},
        {"competitor_slug": "victoriana", "tier": "local", "headline": "", "body_text": "", "cls": {}},
        {"competitor_slug": "mkt-x", "tier": "mercado", "headline": "Celulares en cuotas", "body_text": "", "cls": {}},
        {"competitor_slug": "mkt-y", "tier": "mercado", "headline": "", "body_text": "", "cls": {"summary": "Sillón de living con descuento"}},
    ]
    out = filter_items(items, ["jardin", "sillon"], {"sodimac"})
    assert [(i["competitor_slug"], i["headline"]) for i in out] == [("sodimac", "Juego de jardín"), ("victoriana", ""), ("mkt-y", "")]


def test_every_client_has_rubro():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "config"
    for d in [root] + sorted(p for p in (root / "clients").iterdir() if p.is_dir()):
        assert load_client(d / "competitors.yaml").focus_keywords, d.name
        for c in load_competitors(d / "competitors.yaml"):
            if c.generalist:
                assert c.focus_keywords, (d.name, c.slug)


def test_ai_marks_off_topic_for_any_competitor():
    items = [{"competitor_slug": "victoriana", "tier": "local", "headline": "", "cls": {"product": "otro_rubro"}},
             {"competitor_slug": "victoriana", "tier": "local", "headline": "", "cls": {"product": "parrillas"}},
             {"competitor_slug": "adeleste", "tier": "own", "headline": "", "cls": {"product": "otro_rubro"}}]
    assert [i["cls"]["product"] for i in filter_items(items, [], set())] == ["parrillas", "otro_rubro"]
