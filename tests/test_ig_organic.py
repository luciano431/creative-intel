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
