"""Resultados propios del cliente (Marketing API) sin red: parseo y agregación por mensaje."""
from creative_intel.analysis.own_performance import own_performance
from creative_intel.collectors.meta_insights import performance_row, pick_result


def test_result_preference_and_row():
    assert pick_result([{"action_type": "link_click", "value": "40"}, {"action_type": "lead", "value": "4"}]) == ("leads", 4.0)
    row = performance_row({"spend": "100", "impressions": "10000", "clicks": "150", "ctr": "1.5",
                           "actions": [{"action_type": "purchase", "value": "5"}], "action_values": [{"action_type": "purchase", "value": "400"}]})
    assert row["result_type"] == "compras" and row["cost_per_result"] == 20.0 and row["roas"] == 4.0


def _own(i, fam, spend, clicks, results, fmt="imagen"):
    perf = {"spend": spend, "impressions": 1000, "clicks": clicks, "ctr": clicks / 10, "result_type": "leads", "results": results,
            "cost_per_result": round(spend / results, 2) if results else None, "roas": None, "period": {"since": "2026-09-01", "until": "2026-10-01"}}
    return {"id": i, "tier": "own", "format": fmt, "headline": f"ad {i}", "cls": {"angle_family": fam}, "engagement": {"performance": perf}}


def test_own_performance_groups_by_message_and_ranks_efficiency():
    out = own_performance([_own(1, "proteccion", 100, 20, 10), _own(2, "economico", 50, 5, 1), _own(3, "proteccion", 50, 10, 5, fmt="video"),
                           {"id": 9, "tier": "local", "engagement": {}}])
    fam = {r["value"]: r for r in out["by_family"]}
    assert fam["proteccion"]["ads"] == 2 and fam["proteccion"]["cost_per_result"] == 10.0 and fam["proteccion"]["ctr"] == 1.5
    assert out["best"][0]["id"] in (1, 3) and out["total"]["results"] == 16 and out["result_type"] == "leads"
    assert own_performance([{"id": 1, "tier": "own", "engagement": {}}]) is None


def test_insights_collector_with_fake_graph_api():
    import httpx

    from creative_intel.collectors.meta_insights import MetaInsightsCollector
    from creative_intel.config import CompetitorConfig

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/insights"):
            return httpx.Response(200, json={"data": [
                {"ad_id": "11", "ad_name": "Pisos A", "spend": "80", "impressions": "8000", "clicks": "120", "ctr": "1.5",
                 "actions": [{"action_type": "lead", "value": "8"}]},
                {"ad_id": "22", "ad_name": "Pisos B", "spend": "20", "impressions": "3000", "clicks": "15", "ctr": "0.5", "actions": []}]})
        return httpx.Response(200, json={"id": req.url.path.rsplit("/", 1)[-1], "effective_status": "ACTIVE", "created_time": "2026-09-10T10:00:00-0300",
                                         "creative": {"title": "Pisos técnicos", "body": "Instalación propia", "object_type": "VIDEO"}})

    col = MetaInsightsCollector(token="t", client=httpx.Client(transport=httpx.MockTransport(handler)))
    own = CompetitorConfig(slug="verona", name="Verona", country="UY", tier="own", meta_ad_account_id="123")
    assert col.supports(own) and not col.supports(CompetitorConfig(slug="x", name="X", country="UY", tier="local"))
    raws = col.collect(own)
    assert [r.external_id for r in raws] == ["own-11", "own-22"]
    assert raws[0].engagement["performance"]["cost_per_result"] == 10.0 and raws[0].format == "video"


def test_several_ad_accounts_are_summed():
    import httpx

    from creative_intel.collectors.meta_insights import MetaInsightsCollector
    from creative_intel.config import CompetitorConfig

    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/insights"):
            acct = req.url.path.split("/")[-2]
            seen.append(acct)
            rows = [{"ad_id": acct[-1], "ad_name": "x", "spend": "10", "impressions": "100", "clicks": "1", "ctr": "1", "actions": []}] if acct == "act_2" else []
            return httpx.Response(200, json={"data": rows})
        return httpx.Response(200, json={"id": "2", "creative": {}})

    assert MetaInsightsCollector.accounts("act_1, 2") == ["act_1", "act_2"]
    col = MetaInsightsCollector(token="t", client=httpx.Client(transport=httpx.MockTransport(handler)))
    raws = col.collect(CompetitorConfig(slug="c", name="C", country="UY", tier="own", meta_ad_account_id="act_1,act_2"))
    assert seen == ["act_1", "act_2"] and len(raws) == 1
