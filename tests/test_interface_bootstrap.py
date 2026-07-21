import pytest
from fastapi.testclient import TestClient

from egregore.interface.bootstrap import create_app


@pytest.fixture
def client():
    return TestClient(create_app(), base_url="http://localhost")


def test_ready_route(client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_live_route(client):
    r = client.get("/health/live")
    assert r.status_code == 200


def test_nodes_health_route(client, monkeypatch):
    import os

    os.environ["BLACKSTAR_CLUSTER_NODES"] = (
        "pioneer1=127.0.0.1:8080,pioneer2=192.168.1.102:1,pioneer3=192.168.1.103:1"
    )

    class MockResponse:
        def __init__(self, status_code):
            self.status_code = status_code

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            if ":8080" in url:
                return MockResponse(200)
            raise Exception("connection refused")

    monkeypatch.setattr("httpx.AsyncClient", MockAsyncClient)
    r = client.get("/health/nodes")
    assert r.status_code == 200
    data = r.json()
    assert "online_count" in data
    assert "total" in data
    assert "nodes" in data
    assert data["total"] == 3
    assert data["online_count"] == 1
    pioneer1 = next(n for n in data["nodes"] if n["node_id"] == "pioneer1")
    assert pioneer1["status"] == "online"
    pioneer2 = next(n for n in data["nodes"] if n["node_id"] == "pioneer2")
    assert pioneer2["status"] == "offline"


def test_dossier_generate(client):
    payload = {
        "actor": {"organization_id": "org_1", "actor_id": "actor_1"},
        "causality_id": "cmd-1",
        "engine_version": "engine_vA",
        "policy_version": "policy_v1",
        "input_fingerprint": "fp_1",
        "timestamp_ns": 0,
    }
    valid_key = "a" * 64
    r = client.post(
        "/api/v1/dossier/generate", json=payload, headers={"X-API-Key": valid_key}
    )
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    assert "trace_id" in r.json()


def test_bootstrap_registers_all_routes():
    app = create_app()
    assert app is not None


def test_login_page_is_public(client):
    r = client.get("/dashboard/login")
    assert r.status_code == 200
    assert "OPERATOR LOGIN" in r.text


def test_static_assets_are_public(client):
    r = client.get("/static/css/dashboard.css")
    assert r.status_code == 200


def test_dashboard_requires_auth(client):
    r = client.get("/dashboard")
    assert r.status_code == 401


def test_login_sets_cookie_and_redirects(client):
    valid_key = "a" * 64
    r = client.post(
        "/dashboard/login", data={"api_key": valid_key}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"
    assert "api_key" in r.cookies
    assert r.cookies["api_key"] == valid_key


def test_invalid_login_redirects_without_cookie(client):
    r = client.post(
        "/dashboard/login", data={"api_key": "not-a-valid-key"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard/login?error=invalid"
    assert "api_key" not in r.cookies


def test_dashboard_with_cookie_succeeds(client):
    valid_key = "a" * 64
    client.cookies.set("api_key", valid_key)
    r = client.get("/dashboard")
    assert r.status_code == 200


def test_logout_clears_cookie(client):
    valid_key = "a" * 64
    # Log in to obtain a session cookie from the app
    client.post("/dashboard/login", data={"api_key": valid_key}, follow_redirects=False)
    assert client.cookies.get("api_key") == valid_key
    assert client.get("/dashboard").status_code == 200

    # Log out and confirm subsequent dashboard access is rejected
    r = client.get("/dashboard/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard/login"
    assert client.get("/dashboard").status_code == 401
