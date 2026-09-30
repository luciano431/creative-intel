from creative_intel.config import load_competitors, load_taxonomy


def test_competitors_flatten_with_tiers():
    comps = load_competitors()
    tiers = {c.tier for c in comps}
    assert tiers == {"own", "local", "global"}
    assert len([c for c in comps if c.tier == "local"]) == 5
    assert len([c for c in comps if c.tier == "global"]) == 5
    own = next(c for c in comps if c.tier == "own")
    assert own.slug == "miseguro" and own.is_own
    axa = next(c for c in comps if c.slug == "axa")
    assert axa.ad_countries == ["ES", "FR"]
    bse = next(c for c in comps if c.slug == "bse")
    assert bse.ad_countries == ["UY"]  # default al país


def test_taxonomy_closed_and_families(taxonomy):
    assert "robo" in taxonomy.angles
    assert taxonomy.family_of("robo") == "proteccion"
    assert taxonomy.family_of("descuento") == "oferta"
    assert taxonomy.family_of("inexistente") is None
    assert len(set(taxonomy.angles)) == len(taxonomy.angles), "ángulos duplicados entre familias"
