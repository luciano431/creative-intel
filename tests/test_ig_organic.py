import datetime as dt

import httpx

import pytest

from creative_intel.collectors.ig_organic import InstagramOrganic, find_handle, summarize


@pytest.fixture(autouse=True)
def _cache_aparte(tmp_path, monkeypatch):
    monkeypatch.setattr("creative_intel.collectors.ig_organic.IG_CACHE_DIR", tmp_path / "ig_cache")


def test_find_handle_skips_post_links():
    html = '<a href="https://www.instagram.com/p/Cx1/">post</a> <a href="https://instagram.com/adeleste.uy/">IG</a>'
    assert find_handle(html) == "adeleste.uy"


def test_profile_paginates_until_window_and_summarizes():
    pages = {
        "": [{"id": "1", "caption": "Ciberlunes 30% off", "like_count": 90, "comments_count": 10, "timestamp": "2026-06-02T12:00:00+0000",
              "media_type": "IMAGE"},
             {"id": "2", "caption": "Nueva parrilla", "like_count": 20, "comments_count": 0, "timestamp": "2026-05-20T12:00:00+0000",
              "media_type": "VIDEO", "media_product_type": "REELS"}],
        "c1": [{"id": "3", "caption": "viejo", "like_count": 5, "comments_count": 0, "timestamp": "2026-04-01T12:00:00+0000", "media_type": "IMAGE"}],
    }

    def handler(req: httpx.Request) -> httpx.Response:
        f = req.url.params.get("fields", "")
        if req.url.path.endswith("/me/accounts"):
            return httpx.Response(200, json={"data": [{"instagram_business_account": {"id": "IG1"}}]})
        cur = "c1" if ".after(c1)" in f else ""
        body = {"business_discovery": {"username": "rival", "followers_count": 1000, "media_count": 3,
                                       "media": {"data": pages[cur], "paging": {"cursors": {"after": "c1" if not cur else ""}}}}}
        return httpx.Response(200, json=body, headers={"content-type": "application/json"})

    ig = InstagramOrganic(token="t", client=httpx.Client(transport=httpx.MockTransport(handler)))
    prof = ig.profile("rival", since=dt.date(2026, 5, 11), until=dt.date(2026, 6, 10))
    assert [p["id"] for p in prof["posts"]] == ["1", "2"] and prof["followers_count"] == 1000
    s = summarize(prof, ["ciberlunes"])
    assert s["posts"] == 2 and s["event_posts"] == 1 and s["avg_likes"] == 55 and s["avg_rate"] == 6.0
    assert s["top"][0]["caption"].startswith("Ciberlunes") and s["types"] == {"imagen": 1, "reel": 1}


def test_competitor_falls_back_to_public_profile_and_merges_archive():
    from creative_intel.collectors.ig_organic import merge_archive, raw_posts

    def graph(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/me/accounts"):
            return httpx.Response(200, json={"data": [{"instagram_business_account": {"id": "IG1", "username": "cliente"}}]})
        return httpx.Response(400, json={"error": {"message": "(#10) Application does not have permission for this action"}})

    def public(req: httpx.Request) -> httpx.Response:
        assert req.url.params["username"] == "rival" and req.headers["x-ig-app-id"]
        edges = [{"node": {"id": "9", "shortcode": "AB", "taken_at_timestamp": 1780000000, "__typename": "GraphSidecar",
                           "edge_media_to_caption": {"edges": [{"node": {"text": "CyberLunes 20% off"}}]},
                           "edge_liked_by": {"count": 40}, "edge_media_to_comment": {"count": 2}, "display_url": "http://img"}}]
        return httpx.Response(200, json={"data": {"user": {"username": "rival", "full_name": "Rival", "edge_followed_by": {"count": 2000},
                                                           "edge_owner_to_timeline_media": {"count": 300, "edges": edges}}}})

    ig = InstagramOrganic(token="t", client=httpx.Client(transport=httpx.MockTransport(graph)),
                          public_client=httpx.Client(transport=httpx.MockTransport(public)))
    since, until = dt.date(2026, 5, 1), dt.date(2026, 6, 30)
    prof = ig.profile("rival", since=since, until=until)
    assert prof["source"] == "perfil_publico" and prof["followers_count"] == 2000
    assert prof["posts"][0]["media_type"] == "CAROUSEL_ALBUM" and prof["posts"][0]["permalink"].endswith("/p/AB/")
    old = {"id": "5", "caption": "archivado", "like_count": 10, "comments_count": 0, "timestamp": "2026-05-20T10:00:00+00:00"}
    merged = merge_archive(prof, [old, raw_posts(prof)[0], {**old, "id": "6", "timestamp": "2026-01-01T00:00:00+00:00"}], since, until)
    assert [p["id"] for p in merged["posts"]] == ["9", "5"] and merged["archived"] == 1
    s = summarize(merged, ["cyberlunes"])
    assert s["source"] == "perfil_publico" and s["posts"] == 2 and s["event_posts"] == 1


def test_competitor_uses_apify_back_to_event_date(monkeypatch):
    from creative_intel.config import settings

    monkeypatch.setattr(settings, "apify_token", "apify_x")
    monkeypatch.setattr(settings, "apify_max_posts", 50)

    def graph(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/me/accounts"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(400, json={"error": {"message": "(#10) Application does not have permission for this action"}})

    seen = {}

    def apify(req: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(req.content))
        assert req.url.params["token"] == "apify_x"
        return httpx.Response(200, json=[
            {"id": "1", "shortCode": "A", "caption": "Ciberlunes en Tienda", "timestamp": "2026-06-02T15:00:00.000Z", "likesCount": 120,
             "commentsCount": 8, "type": "Sidecar", "url": "https://www.instagram.com/p/A/", "displayUrl": "http://img"},
            {"id": "2", "shortCode": "B", "caption": "octubre", "timestamp": "2026-10-01T15:00:00.000Z", "likesCount": -1,
             "commentsCount": 1, "type": "Video", "productType": "clips"}])

    def public(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"user": {"username": "rival", "edge_followed_by": {"count": 4000},
                                                           "edge_owner_to_timeline_media": {"edges": []}}}})

    ig = InstagramOrganic(token="t", client=httpx.Client(transport=httpx.MockTransport(graph)),
                          public_client=httpx.Client(transport=httpx.MockTransport(public)),
                          apify_client=httpx.Client(transport=httpx.MockTransport(apify)))
    prof = ig.profile("rival", since=dt.date(2026, 5, 11), until=dt.date(2026, 6, 10))
    assert seen["onlyPostsNewerThan"] == "2026-05-11" and seen["resultsLimit"] == 50
    assert prof["source"] == "apify" and prof["followers_count"] == 4000 and [p["id"] for p in prof["posts"]] == ["1"]
    assert prof["posts"][0]["media_type"] == "CAROUSEL_ALBUM" and summarize(prof, ["ciberlunes"])["event_posts"] == 1


def test_apify_reports_missing_account():
    import pytest

    from creative_intel.collectors.ig_organic import apify_profile

    def apify(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"url": "https://www.instagram.com/noexiste/", "error": "not_found",
                                          "errorDescription": "Page not found"}])

    with pytest.raises(RuntimeError, match="noexiste: Page not found"):
        apify_profile("noexiste", dt.date(2026, 5, 1), None, "x", client=httpx.Client(transport=httpx.MockTransport(apify)))


def test_apify_followers_from_details_when_public_fails(monkeypatch):
    from creative_intel.collectors import ig_organic
    from creative_intel.collectors.ig_organic import apify_profile

    monkeypatch.setattr(ig_organic, "APIFY_RETRY_WAIT", 0)

    calls = []

    def apify(req: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(req.content)
        calls.append(body["resultsType"])
        if body["resultsType"] == "details":
            assert "onlyPostsNewerThan" not in body
            return httpx.Response(200, json=[{"username": "rival", "followersCount": 5300}])
        if len(calls) == 1:
            return httpx.Response(502, text="<html>502 Bad Gateway</html>")
        return httpx.Response(200, json=[{"id": "1", "caption": "x", "timestamp": "2026-06-01T10:00:00.000Z", "likesCount": 3}])

    def public(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "login required"})

    prof = apify_profile("rival", dt.date(2026, 5, 1), None, "x", client=httpx.Client(transport=httpx.MockTransport(apify)),
                         followers_client=httpx.Client(transport=httpx.MockTransport(public)))
    assert calls == ["posts", "posts", "details"] and prof["followers_count"] == 5300 and len(prof["posts"]) == 1


def test_apify_pays_each_post_once(tmp_path):
    from creative_intel.collectors.ig_organic import apify_cached

    calls = []

    def apify(req: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(req.content)
        if body["resultsType"] == "details":
            return httpx.Response(200, json=[{"followersCount": 100}])
        calls.append(body.get("onlyPostsNewerThan"))
        posts = [{"id": "a", "timestamp": "2025-11-04T10:00:00.000Z", "caption": "CyberLunes", "likesCount": 5},
                 {"id": "b", "timestamp": "2026-10-01T10:00:00.000Z", "caption": "hoy", "likesCount": 1}]
        if body.get("onlyPostsNewerThan", "") >= "2026-10-05":
            posts = [{"id": "c", "timestamp": "2026-10-06T10:00:00.000Z", "caption": "nuevo", "likesCount": 2}]
        return httpx.Response(200, json=posts)

    def public(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    c = httpx.Client(transport=httpx.MockTransport(apify))
    pc = httpx.Client(transport=httpx.MockTransport(public))
    d = dt.date
    p1 = apify_cached("rival", d(2025, 11, 3), d(2025, 11, 5), "x", 400, c, pc, today=d(2026, 10, 5))
    assert [x["id"] for x in p1["posts"]] == ["a"] and p1["paid_posts"] == 2 and calls == ["2025-11-03"]
    p2 = apify_cached("rival", d(2025, 11, 3), d(2025, 11, 5), "x", 400, c, pc, today=d(2026, 10, 6))
    assert [x["id"] for x in p2["posts"]] == ["a"] and p2["paid_posts"] == 0 and calls == ["2025-11-03"]  # no volvió a pagar
    p3 = apify_cached("rival", d(2026, 9, 1), d(2026, 10, 6), "x", 400, c, pc, today=d(2026, 10, 6))
    assert calls == ["2025-11-03", "2026-10-05"] and sorted(x["id"] for x in p3["posts"]) == ["b", "c"]  # solo lo nuevo


def test_cached_only_never_calls_apify(tmp_path):
    import json

    from creative_intel.collectors import ig_organic

    assert ig_organic.cached_only("rival", dt.date(2024, 11, 4), dt.date(2024, 11, 6)) is None  # sin archivo: no se baja
    ig_organic.IG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (ig_organic.IG_CACHE_DIR / "rival.json").write_text(json.dumps({"username": "rival", "covered_since": "2024-10-01", "posts": [
        {"id": "1", "timestamp": "2024-11-05T10:00:00", "caption": "CyberLunes"}, {"id": "2", "timestamp": "2025-01-01T10:00:00", "caption": "x"}]}))
    got = ig_organic.cached_only("rival", dt.date(2024, 11, 4), dt.date(2024, 11, 6))
    assert [p["id"] for p in got["posts"]] == ["1"]
    assert ig_organic.cached_only("rival", dt.date(2024, 9, 1), dt.date(2024, 9, 5)) is None  # el archivo no llega
