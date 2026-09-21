"""Tests for the user management and auth HTTP endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_key() -> str:
    return "a" * 64


@pytest.fixture
def client(monkeypatch, tmp_path, admin_key):
    """Build a TestClient with a fresh temp DB and a seeded admin API key."""
    monkeypatch.setenv("EGREGORE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EGREGORE_NODE_ID", "testnode")
    monkeypatch.setenv("EGREGORE_API_KEYS", f"{admin_key}:test:admin:admin")
    monkeypatch.setenv("EGREGORE_ZARC_SIGNING_KEY_HEX", "a" * 64)

    # Clear any cached default repository from previous tests.
    import egregore.infrastructure.persistence.user_repository as user_repo_mod

    user_repo_mod._default_repo = None

    from egregore.http_api.http.app import create_app

    app = create_app(build_container=False)
    return TestClient(app)


@pytest.fixture
def admin_headers(admin_key) -> dict:
    return {"X-API-Key": admin_key}


def test_invite_requires_admin(client, admin_headers):
    resp = client.post("/admin/invite", json={"role": "user"}, headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"].startswith("INV-")
    assert data["role"] == "user"


def test_invite_denied_for_non_admin(client, monkeypatch):
    # Simulate a guest API key registered in the middleware key table.
    guest_key = "b" * 64
    import egregore.http_api.http.middleware.api_key_middleware as mw

    monkeypatch.setitem(mw._API_KEYS, guest_key, ("test", "guest_user", "guest"))
    resp = client.post(
        "/admin/invite", json={"role": "user"}, headers={"X-API-Key": guest_key}
    )
    assert resp.status_code == 403


def test_create_user_and_grant(client, admin_headers):
    resp = client.post(
        "/admin/users",
        json={
            "username": "bob",
            "email": "bob@example.com",
            "role": "user",
            "verticals": ["sweng_python"],
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "bob"
    assert data["roles"] == ["user"]

    user_id = data["id"]
    resp = client.get(f"/admin/users/{user_id}", headers=admin_headers)
    assert resp.status_code == 200
    detail = resp.json()
    assert any(
        g["cell_id"] == "sweng_python" and g["permission"] == "write"
        for g in detail["vertical_grants"]
    )


def test_list_users(client, admin_headers):
    client.post(
        "/admin/users",
        json={"username": "carol", "role": "guest"},
        headers=admin_headers,
    )
    resp = client.get("/admin/users", headers=admin_headers)
    assert resp.status_code == 200
    usernames = {u["username"] for u in resp.json()["users"]}
    assert "admin" in usernames or "carol" in usernames
