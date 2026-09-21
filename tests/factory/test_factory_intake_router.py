from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from egregore.interface.factory_router import router


@pytest.fixture
def client():
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/factory")
    return TestClient(app)


def test_intake_endpoint_creates_envelope(client):
    response = client.post(
        "/api/v1/factory/v1/intake",
        json={
            "source_type": "chat",
            "text": "Explain hearsay",
            "task_type": "chat",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["source"]["source_type"] == "chat"
    assert data["payload"]["text"] == "Explain hearsay"
    assert data["task_type"] == "chat"


def test_intake_chat_endpoint(client):
    response = client.post(
        "/api/v1/factory/v1/intake/chat",
        json={"role": "user", "content": "Hello"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["source"]["source_type"] == "chat"
    assert data["payload"]["text"] == "Hello"


def test_intake_email_endpoint(client):
    response = client.post(
        "/api/v1/factory/v1/intake/email",
        json={
            "message_id": "<m1>",
            "subject": "Complaint",
            "body_plain": "I want to file a complaint.",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["source"]["source_type"] == "email"
    assert data["payload"]["subject"] == "Complaint"
