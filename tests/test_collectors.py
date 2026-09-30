import datetime as dt

from creative_intel.collectors.google_serpapi import GoogleSerpApiCollector
from creative_intel.collectors.manual import row_to_raw
from creative_intel.collectors.meta_api import MetaAdLibraryApiCollector
from creative_intel.collectors.meta_web import parse_human_date
from creative_intel.config import CompetitorConfig


def _comp(**kw):
    base = dict(slug="axa", name="AXA", country="UY", tier="global", meta_page_id="123", google_advertiser_id="AR1", ad_countries=["ES"])
    base.update(kw)
    return CompetitorConfig(**base)


def test_meta_api_mapping_marks_active_when_no_stop_time():
    ad = {"id": "555", "ad_creative_bodies": ["Cuerpo"], "ad_creative_link_titles": ["Titulo"], "ad_delivery_start_time": "2026-08-01",
          "publisher_platforms": ["facebook", "instagram"], "ad_snapshot_url": "https://fb/ad/555"}
    raw = MetaAdLibraryApiCollector.to_raw(ad, _comp())
    assert raw.external_id == "555" and raw.is_active and raw.first_seen == dt.date(2026, 8, 1)
    assert raw.placements == ["feed"] and raw.headline == "Titulo" and raw.countries == ["ES"]
    raw2 = MetaAdLibraryApiCollector.to_raw({**ad, "ad_delivery_stop_time": "2026-08-20T10:00:00+0000"}, _comp())
    assert not raw2.is_active and raw2.last_seen == dt.date(2026, 8, 20)


def test_serpapi_mapping_formats_to_placements():
    ad = {"ad_id": "CR1", "format": "text", "title": "Seguro", "text": "Cotizá", "target_domain": "axa.es", "first_shown": "2026-07-01", "last_shown": "2026-09-01"}
    raw = GoogleSerpApiCollector.to_raw(ad, _comp(), "ES")
    assert raw.format == "texto" and raw.placements == ["search"] and raw.first_seen == dt.date(2026, 7, 1)
    vid = GoogleSerpApiCollector.to_raw({**ad, "format": "video", "video": "https://yt/x"}, _comp(), "ES")
    assert vid.format == "video" and vid.placements == ["youtube"] and vid.media_urls == ["https://yt/x"]


def test_manual_row_parsing_and_engagement():
    raw = row_to_raw({"competitor_slug": "bse", "platform": "meta", "placements": "feed|stories", "likes": "10", "comments": "", "first_seen": "12/08/2026", "is_active": "false"})
    assert raw.placements == ["feed", "stories"] and raw.engagement == {"likes": 10}
    assert raw.first_seen == dt.date(2026, 8, 12) and raw.is_active is False


def test_parse_human_dates_en_and_es():
    assert parse_human_date("Sep 12, 2026") == dt.date(2026, 9, 12)
    assert parse_human_date("12 de septiembre de 2026") == dt.date(2026, 9, 12)
    assert parse_human_date("1 ago 2026") == dt.date(2026, 8, 1)
    assert parse_human_date("sin fecha") is None


def test_basecamp_next_link_parsing():
    from creative_intel.publish.basecamp_auth import _next_link

    assert _next_link('<https://3.basecampapi.com/1/projects.json?page=2>; rel="next"') == "https://3.basecampapi.com/1/projects.json?page=2"
    assert _next_link("") is None


def test_parse_meta_card_text_strips_ui_noise_and_splits_fields():
    from creative_intel.collectors.meta_web import parse_card_text

    card = """
Identificador de la biblioteca: 1028284793592494
Comenzó a publicarse el 31 ago 2026
Plataformas
Este anuncio tiene varias versiones

Abrir menú desplegable

Seguros SURA Uruguay
¿Qué más podés pedir?
Pedí SURA y contratá tu Seguro de Auto 🚗.
Disfrutá de cobertura sin letra chica.
SEGUROS.SURA.COM.UY
Seguro de Auto SURA
Cotizá online en minutos
Más información
"""
    p = parse_card_text(card, "SURA Uruguay")
    assert p["body"] == "¿Qué más podés pedir?\nPedí SURA y contratá tu Seguro de Auto 🚗.\nDisfrutá de cobertura sin letra chica."
    assert p["landing"] == "seguros.sura.com.uy"
    assert p["headline"] == "Seguro de Auto SURA"
    assert p["description"] == "Cotizá online en minutos"
    assert p["cta"] == "Más información"
    assert p["multiple_versions"] is True
    # Una línea de copy que contiene "activo" no se descarta
    assert "activo" in parse_card_text("Seguros SURA Uruguay\nMantené tu auto activo todo el año", "SURA Uruguay")["body"]


def test_parse_meta_card_text_real_sample_without_domain():
    from creative_intel.collectors.meta_web import parse_card_text

    card = ("\u200b\nActivo\nIdentificador de la biblioteca: 1864281568316131\nEn circulación desde el 31 ago 2026\nPlataformas\n"
            "\u200b\n\u200b\nEste anuncio tiene varias versiones\n\u200b\nAbrir menú desplegable\n\u200b\nVer detalles del anuncio\n"
            "Seguros SURA Uruguay\nPublicidad\n¿Qué más podés pedir?\nPedí SURA y contratá tu Seguro de Auto 🚗. \n\n"
            "Disfrutá de una protección integral en hasta 10 cuotas sin recargo.\n\n👉 Cotizá tu cobertura.\n"
            "Tu Seguro de Auto, con más beneficios\nGet Quote")
    p = parse_card_text(card, "SURA Uruguay")
    assert p["body"].startswith("¿Qué más podés pedir?")
    assert p["body"].endswith("👉 Cotizá tu cobertura.")
    assert "Publicidad" not in p["body"] and "Activo" not in p["body"]
    assert p["headline"] == "Tu Seguro de Auto, con más beneficios"
    assert p["cta"] == "Get Quote" and p["multiple_versions"] is True


def test_parse_meta_card_text_skips_page_name_after_details_marker():
    from creative_intel.collectors.meta_web import parse_card_text

    card = "Activo\nIdentificador de la biblioteca: 1\nVer detalles del anuncio\nBSE\nPublicidad\nTu yo del futuro te lo va a agradecer\nPortal Comercial\nLearn More"
    p = parse_card_text(card, "Banco de Seguros del Estado (BSE)")
    assert p["body"] == "Tu yo del futuro te lo va a agradecer"
    assert p["headline"] == "Portal Comercial" and p["cta"] == "Learn More"


def test_parse_meta_card_text_video_timestamps_and_scheme_domain():
    from creative_intel.collectors.meta_web import parse_card_text

    card = "Ver detalles del anuncio\nAXA\nWir sorgen uns um vieles.\n0:00\n0:20\n0:00 / 0:20\nHTTPS://LANDINGPAGE.AXA.DE/RISIKO\nDie wahren Risiken\nLearn More"
    p = parse_card_text(card, "AXA")
    assert p["body"] == "Wir sorgen uns um vieles."
    assert p["landing"] == "landingpage.axa.de/risiko" and p["headline"] == "Die wahren Risiken" and p["cta"] == "Learn More"


def test_google_detail_dates_from_page_text():
    from creative_intel.collectors.google_web import _date_after, domain_of, region_param
    from creative_intel.config import CompetitorConfig

    text = "Formato: Imagen\nSe publicó por primera vez el 12 sep 2026\nSe publicó por última vez el 30 sep 2026\nRegiones: Uruguay"
    assert _date_after(text, r"(primera vez|first shown)") == dt.date(2026, 9, 12)
    assert _date_after(text, r"(última vez|last shown)") == dt.date(2026, 9, 30)
    assert _date_after("First shown: Sep 12, 2026", r"(first shown)") == dt.date(2026, 9, 12)
    c = CompetitorConfig(slug="bse", name="BSE", country="UY", tier="local", website="https://www.bse.com.uy/seguros")
    assert domain_of(c) == "bse.com.uy" and region_param("ALL") == "anywhere" and region_param("uy") == "UY"


def test_google_clean_lines_drops_chrome_and_advertiser_name():
    from creative_intel.collectors.google_web import clean_lines

    raw = ["MAPFRE URUGUAY SEGUROS S.A.", "La información acerca de este anuncio puede variar según la ubicación", "30 sept 2026",
           "Anuncio", "www.mapfre.com.uy", "Seguro de Auto MAPFRE", "Cotizá online en minutos", "Privacidad", "Blog de Ads",
           "Se quitó debido a un incumplimiento de política", "hide_image"]
    assert clean_lines(raw, "MAPFRE Uruguay") == ["www.mapfre.com.uy", "Seguro de Auto MAPFRE", "Cotizá online en minutos"]


def test_google_youtube_id_extraction():
    from creative_intel.collectors.google_web import YT_ID_RE

    u = "https://www.youtube.com/embed/tksxh4uifec?rel=0&version=3&controls=0"
    assert YT_ID_RE.search(u).group(1) == "tksxh4uifec"
    assert YT_ID_RE.search("https://tpc.googlesyndication.com/pagead/gadgets/youtube_player_media/x.html") is None


def test_google_declared_total():
    from creative_intel.collectors.google_web import _declared_total

    assert _declared_total("Nombre legal:\\n4 anuncios\\ncalendar_today") == 4
    assert _declared_total("~1.234 anuncios") == 1234
    assert _declared_total("Más de 2 mil anuncios") == 2000
    assert _declared_total("sin datos") is None


def test_google_ids_from_rpc():
    from creative_intel.collectors.google_web import ids_from_rpc

    body = '{"1":[{"1":"AR05336417929390456833","2":"CR10149519646189944833","3":{}},{"1":"AR05336417929390456833","2":"CR01758643388061581313"}]}'
    assert ids_from_rpc(body) == [("AR05336417929390456833", "CR10149519646189944833"), ("AR05336417929390456833", "CR01758643388061581313")]
