import datetime as dt

from creative_intel.collectors.base import RawCreative
from creative_intel.db import Creative, Run, Sighting, get_or_create_run
from creative_intel.normalize import dedupe, fingerprint, mark_unseen_inactive, upsert_creatives


def _raw(**kw):
    base = dict(competitor_slug="sura-uy", platform="meta", source="manual", body_text="Hola", is_active=True)
    base.update(kw)
    return RawCreative(**base)


def test_fingerprint_prefers_external_id():
    a = _raw(external_id="1", body_text="x")
    b = _raw(external_id="1", body_text="otro texto")
    c = _raw(external_id="2", body_text="x")
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) != fingerprint(c)


def test_fingerprint_without_id_uses_normalized_text_and_media():
    a = _raw(body_text="  Cotizá   AHORA ", media_urls=["http://m/1.jpg"])
    b = _raw(body_text="cotizá ahora", media_urls=["http://m/1.jpg"])
    c = _raw(body_text="cotizá ahora", media_urls=["http://m/2.jpg"])
    assert fingerprint(a) == fingerprint(b) != fingerprint(c)


def test_dedupe_merges_placements_and_dates():
    a = _raw(external_id="9", placements=["feed"], first_seen=dt.date(2026, 8, 10))
    b = _raw(external_id="9", placements=["stories"], first_seen=dt.date(2026, 8, 1))
    out = dedupe([a, b])
    assert len(out) == 1
    assert out[0].placements == ["feed", "stories"]
    assert out[0].first_seen == dt.date(2026, 8, 1)


def test_upsert_tracks_longevity_and_sightings(session):
    run1 = get_or_create_run(session, "2026-08")
    seen1 = dt.date(2026, 8, 25)
    stats = upsert_creatives(session, run1, [_raw(external_id="A", first_seen=dt.date(2026, 8, 1)), _raw(external_id="B")], seen1)
    assert stats["new"] == 2
    a = session.query(Creative).filter_by(external_id="A").one()
    b = session.query(Creative).filter_by(external_id="B").one()
    assert a.first_seen == dt.date(2026, 8, 1) and a.last_seen == seen1 and a.days_active == 24
    assert b.first_seen == seen1  # sin fecha declarada: primera vez que lo vimos

    run2 = get_or_create_run(session, "2026-09")
    seen2 = dt.date(2026, 9, 25)
    stats = upsert_creatives(session, run2, [_raw(external_id="A", first_seen=dt.date(2026, 8, 1))], seen2)
    assert stats["updated"] == 1
    session.refresh(a)
    assert a.last_seen == seen2 and a.days_active == 55 and a.status == "active"
    assert session.query(Sighting).filter_by(creative_id=a.id).count() == 2

    comp_ids = [a.competitor_id]
    n = mark_unseen_inactive(session, run2, comp_ids, ["meta"])
    session.refresh(b)
    assert n == 1 and b.status == "inactive" and b.last_seen == seen1  # last_seen no retrocede ni avanza


def test_unknown_competitor_is_counted_not_crashed(session):
    run = get_or_create_run(session, "2026-09")
    stats = upsert_creatives(session, run, [_raw(competitor_slug="nadie", external_id="Z")], dt.date(2026, 9, 1))
    assert stats["unknown_competitor"] == 1 and stats["new"] == 0
