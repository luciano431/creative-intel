import datetime as dt

import httpx

from creative_intel.collectors.ig_organic import InstagramOrganic, find_handle, summarize


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
