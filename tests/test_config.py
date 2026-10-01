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
    assert axa.ad_countries == ["ALL"]
    bse = next(c for c in comps if c.slug == "bse")
    assert bse.ad_countries == ["UY"]  # default al país


def test_taxonomy_closed_and_families(taxonomy):
    assert "robo" in taxonomy.angles
    assert taxonomy.family_of("robo") == "proteccion"
    assert taxonomy.family_of("descuento") == "oferta"
    assert taxonomy.family_of("inexistente") is None
    assert len(set(taxonomy.angles)) == len(taxonomy.angles), "ángulos duplicados entre familias"


def test_new_client_scaffold(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from creative_intel import cli, config

    monkeypatch.setattr(config, "CLIENTS_DIR", tmp_path)
    res = CliRunner().invoke(cli.app, ["new-client", "banco-x", "--name", "Banco X", "--industry", "Banca", "--market", "Uruguay"])
    assert res.exit_code == 0, res.output
    comp = tmp_path / "banco-x" / "competitors.yaml"
    assert comp.exists() and (tmp_path / "banco-x" / "taxonomy.yaml").exists()
    ci = config.load_client(comp)
    assert ci.name == "Banco X" and ci.industry == "Banca"
    assert any(c.tier == "own" and c.name == "Banco X" for c in config.load_competitors(comp))


def test_brand_and_labels():
    from creative_intel.config import load_brand, load_taxonomy

    b = load_brand()
    assert b.app_name == "Radar creativo de Ranker" and b.colors["accent"].startswith("#")
    t = load_taxonomy()
    assert t.label("tofu") == "Descubrimiento" and t.label("social_proof") == "Prueba social" and t.label(None) == "Sin dato"
