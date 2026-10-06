"""Buscador de IDs: lectura de respuestas de la Biblioteca de Meta y escritura en competitors.yaml (sin red)."""
import json

import yaml

from creative_intel.discovery import (
    MetaPage, add_competitors, domain_of_url, is_confident, name_match, pages_from_text, rank_pages, update_ids,
)


def _ad(ad_id, page_id, page_name, caption="", link="", likes=None):
    snap = {"caption": caption, "link_url": link, "body": {"text": "x" * 200}}
    if likes is not None:
        snap["page_like_count"] = likes
    return {"ad_archive_id": ad_id, "collation_count": 1, "page_id": page_id, "page_name": page_name, "snapshot": snap}


SAMPLE = json.dumps({"data": {"ad_library_main": {"search_results_connection": {"edges": [
    {"node": {"collated_results": [_ad("111", "294955077801735", "miSeguroOk", "MISEGUROOK.COM", "https://misegurook.com/auto", 5200)]}},
    {"node": {"collated_results": [_ad("112", "294955077801735", "miSeguroOk", "misegurook.com")]}},
    {"node": {"collated_results": [_ad("113", "1672629656389763", "Banco de Seguros del Estado", "", "https://www.bse.com.uy/")]}},
    {"node": {"collated_results": [_ad("114", "999000111", "Comparador Seguros \\u00daY", "", "https://wa.me/598")]}},
]}}}}, ensure_ascii=False)


def test_pages_from_text_groups_ads_names_and_domains():
    pages = pages_from_text(SAMPLE)
    assert set(pages) == {"294955077801735", "1672629656389763", "999000111"}
    own = pages["294955077801735"]
    assert own.name == "miSeguroOk" and len(own.ads) == 2 and own.top_domain == "misegurook.com" and own.likes == 5200
    assert pages["1672629656389763"].top_domain == "bse.com.uy"
    assert pages["999000111"].top_domain == "", "WhatsApp no es el dominio del anunciante"


def test_rank_and_confidence():
    ranked = rank_pages(pages_from_text(SAMPLE), query="mi seguro ok")
    assert ranked[0].page_id == "294955077801735" and is_confident(ranked[0], "mi seguro ok")
    assert name_match("BSE", "Banco de Seguros del Estado") < 0.8, "una sigla no alcanza para darlo por seguro"
    by_ads = rank_pages(pages_from_text(SAMPLE), exclude=["294955077801735"])
    assert [p.page_id for p in by_ads][0] != "294955077801735"


def test_domain_of_url():
    assert domain_of_url("https://www.MiSeguroOk.com/x?y=1") == "misegurook.com"
    assert domain_of_url("MISEGUROOK.COM") == "misegurook.com"
    assert domain_of_url("https://l.facebook.com/l.php?u=x") == ""
    assert domain_of_url("Más información") == ""


def test_update_ids_and_add_competitors(tmp_path):
    path = tmp_path / "competitors.yaml"
    path.write_text(yaml.safe_dump({
        "client": {"slug": "x", "name": "X"},
        "countries": [{"code": "UY", "name": "Uruguay",
                       "own": {"slug": "x", "name": "X", "meta_page_id": ""},
                       "competitors": {"local": [{"slug": "competidor-1", "name": "Competidor 1", "meta_page_id": ""}],
                                       "global": [{"slug": "referente-1", "name": "Referente global 1", "meta_page_id": ""}]}}],
    }), encoding="utf-8")
    assert update_ids(path, {"x": {"meta_page_id": "123", "google_domain": "x.com"}}) == 2
    added = add_competitors(path, [MetaPage("555", "Seguros Uno", ads={"1"}), MetaPage("123", "X")], "local", "UY")
    assert added == ["Seguros Uno"], "no repite al propio cliente"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    uy = data["countries"][0]
    assert uy["own"]["meta_page_id"] == "123" and uy["own"]["google_domain"] == "x.com"
    assert [c["slug"] for c in uy["competitors"]["local"]] == ["seguros-uno"], "saca el ejemplo vacío de la plantilla"
    assert (tmp_path / "competitors.yaml.bak").exists()


def test_name_match_rejects_other_businesses_sharing_words():
    assert not is_confident(MetaPage("1", "Barraca Nuevo Cerro", ads={"a"}), "Cerro Nuevo")
    assert is_confident(MetaPage("2", "Vinibel Uruguay", ads={"a"}), "Vinibel")
    assert is_confident(MetaPage("3", "Sodimac Homecenter", ads={"a"}), "Sodimac")
    assert is_confident(MetaPage("4", "MiseguroOk", ads={"a"}), "miSeguroOk")


def test_name_match_with_other_alphabets_is_not_confident():
    assert not is_confident(MetaPage("5", "한화데이즈(Hanwha)", ads={"a"}), "Hanwha")
    assert is_confident(MetaPage("6", "Construcción Pérez", ads={"a"}), "Construccion Perez"), "las tildes no cuentan"


def test_set_fields_can_clear_values(tmp_path):
    from creative_intel.discovery import set_fields

    path = tmp_path / "competitors.yaml"
    path.write_text(yaml.safe_dump({"countries": [{"code": "UY", "competitors": {"global": [
        {"slug": "hanwha", "name": "Hanwha", "meta_page_id": "204071546359884", "google_domain": "e2i.sg"}]}}]}), encoding="utf-8")
    set_fields(path, "hanwha", {"name": "Headline Flooring", "meta_page_id": "", "google_domain": "headlineflooring.com"})
    e = yaml.safe_load(path.read_text(encoding="utf-8"))["countries"][0]["competitors"]["global"][0]
    assert e == {"slug": "hanwha", "name": "Headline Flooring", "meta_page_id": "", "google_domain": "headlineflooring.com"}
