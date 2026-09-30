import os

os.environ["API_TOKEN"] = "t0ken"

from fastapi.testclient import TestClient  # noqa: E402

from creative_intel import api as apimod  # noqa: E402
from creative_intel.config import settings  # noqa: E402


def test_api_auth_and_status(session):
    settings.api_token = "t0ken"
    client = TestClient(apimod.app)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/runs/2026-09").status_code == 401
    r = client.get("/runs/2026-09", headers={"X-API-Token": "t0ken"})
    assert r.status_code == 200 and r.json()["status"] == "created"
    r = client.post("/runs/2026-09/classify", json={"mock_ai": True}, headers={"X-API-Token": "t0ken"})
    assert r.status_code == 202
