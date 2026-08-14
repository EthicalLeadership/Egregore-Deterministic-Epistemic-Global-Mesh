"""Tests for the ANCHORUM plain-HTTP site and Legal Dossier chat endpoint."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault(
    "EGREGORE_API_KEYS",
    "2c7e17e74e15b30c6813a7bde6ad0be898ef2fdcc1def66046eee6f179d3e7a7:test_tenant:test_user:admin",
)

from fastapi.testclient import TestClient

from egregore.interface import anchorum_http, anchorum_router, case_rag


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with isolated report dir and a fake Core API key."""
    monkeypatch.setattr(anchorum_router, "DEFAULT_REPORT_DIR", tmp_path)
    monkeypatch.setattr(anchorum_router, "READ_ONLY_REPORT_DIRS", [])
    monkeypatch.setenv("EGREGORE_CASE_RAG_ROOT", str(tmp_path / "rag_cases"))
    monkeypatch.setattr(anchorum_http, "_service_api_key", lambda: "fake-key")
    return TestClient(anchorum_http.create_app(), base_url="http://localhost")


def _api_key() -> str:
    return os.environ["EGREGORE_API_KEYS"].split(":")[0]


def _make_case(report_dir: Path, case_id: str = "CHAT-001") -> None:
    anchorum_router._report_dir().mkdir(parents=True, exist_ok=True)
    report = {
        "case_id": case_id,
        "report_id": f"r-{case_id}",
        "artifact_count": 2,
        "entity_count": 1,
        "anomaly_count": 1,
        "critical_findings": [],
        "high_findings": [
            {
                "anomaly_type": "MISSING_PAYMENT",
                "description": "Insurance payment of March 3rd was missed.",
            }
        ],
        "medium_findings": [],
        "low_findings": [],
        "info_findings": [],
        "master_timeline": [],
        "entity_directory": [{"value": "Samuel Tessier"}],
        "audio_transcripts": [],
    }
    import canonicaljson

    (report_dir / f"{case_id}_report.json").write_text(
        canonicaljson.encode_canonical_json(report).decode(), encoding="utf-8"
    )


def _fake_chat_factory(responses: list[str]) -> Any:
    """Return a coroutine that cycles through canned assistant contents."""
    calls: list[list[dict[str, str]]] = []
    idx = 0

    async def _fake(messages: list[dict[str, str]]) -> dict[str, Any]:
        nonlocal idx
        calls.append(messages)
        content = responses[idx % len(responses)]
        idx += 1
        return {
            "message": {"role": "assistant", "content": content},
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            "governance": {},
        }

    _fake.calls = calls  # type: ignore[attr-defined]
    return _fake


def test_chat_legal_without_case(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_chat_factory(["Art. 124 L.N.T. applies."])
    monkeypatch.setattr(anchorum_http, "_chat_with_retries", fake)

    resp = client.post(
        "/api/v1/anchorum/chat",
        json={"message": "What is the legal test?", "mode": "legal"},
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "Art. 124" in data["content"]
    assert data["sources"] == []
    assert data["rag_telemetry"]["retrieved_chunks"] == 0
    assert data["rag_telemetry"]["refusal_retry"] is False


def test_chat_legal_with_case_includes_evidence_and_citations(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_case(tmp_path)
    case_rag.index_case("CHAT-001")

    fake = _fake_chat_factory(["According to [1], the March 3rd payment was missed."])
    monkeypatch.setattr(anchorum_http, "_chat_with_retries", fake)

    resp = client.post(
        "/api/v1/anchorum/chat",
        json={
            "message": "What happened on March 3rd?",
            "mode": "legal",
            "case_id": "CHAT-001",
        },
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["sources"]
    assert all("n" in s and "source" in s for s in data["sources"])
    assert data["rag_telemetry"]["retrieved_chunks"] > 0
    assert data["rag_telemetry"]["citation_missing"] is False

    # Evidence instruction was injected.
    messages = fake.calls[0]
    system = next(m["content"] for m in messages if m["role"] == "system")
    assert "case documents" in system.lower()
    user = next(m["content"] for m in messages if m["role"] == "user")
    assert "CASE DOCUMENTS:" in user
    assert "citing each claim as [1]" in user


def test_chat_refusal_triggers_recovery(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_case(tmp_path)
    case_rag.index_case("CHAT-001")

    fake = _fake_chat_factory(
        [
            "I do not have access to the case documents.",
            "According to [1], the March 3rd payment was missed.",
        ]
    )
    monkeypatch.setattr(anchorum_http, "_chat_with_retries", fake)

    resp = client.post(
        "/api/v1/anchorum/chat",
        json={
            "message": "What happened on March 3rd?",
            "mode": "legal",
            "case_id": "CHAT-001",
        },
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rag_telemetry"]["refusal_retry"] is True
    assert "March 3rd" in data["content"]
    # Refusal recovery note was sent as the second user turn.
    second_call = fake.calls[1]
    assert any(anchorum_http._REFUSAL_RECOVERY_NOTE in m["content"] for m in second_call if m["role"] == "user")


def test_chat_missing_citation_flagged(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_case(tmp_path)
    case_rag.index_case("CHAT-001")

    fake = _fake_chat_factory(["The payment was missed."])
    monkeypatch.setattr(anchorum_http, "_chat_with_retries", fake)

    resp = client.post(
        "/api/v1/anchorum/chat",
        json={
            "message": "What happened on March 3rd?",
            "mode": "legal",
            "case_id": "CHAT-001",
        },
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rag_telemetry"]["citation_missing"] is True


def test_chat_evidence_truncated_to_context_budget(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_case(tmp_path)
    # Add a large staged evidence file so retrieval has something to truncate.
    staged = tmp_path.parent / "fetched" / "CHAT-001"
    staged.mkdir(parents=True)
    big_text = ("payment" + " ") * 5000
    (staged / "big.txt").write_text(big_text, encoding="utf-8")
    case_rag.index_case("CHAT-001", extra_dirs=[str(staged)])

    fake = _fake_chat_factory(["Canned answer with [1]."])
    monkeypatch.setattr(anchorum_http, "_chat_with_retries", fake)
    monkeypatch.setattr(anchorum_http, "_MAX_CONTEXT_CHARS", 4000)

    resp = client.post(
        "/api/v1/anchorum/chat",
        json={
            "message": "payment",
            "mode": "legal",
            "case_id": "CHAT-001",
        },
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 200
    user = next(m["content"] for m in fake.calls[0] if m["role"] == "user")
    assert "[additional evidence truncated for context budget]" in user


def test_index_health_endpoint(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from egregore.interface import anchorum_router, case_rag

    monkeypatch.setattr(anchorum_router, "DEFAULT_REPORT_DIR", tmp_path)
    monkeypatch.setattr(anchorum_router, "READ_ONLY_REPORT_DIRS", [])
    monkeypatch.setenv("EGREGORE_CASE_RAG_ROOT", str(tmp_path / "rag_cases"))

    report = {
        "case_id": "HLTH-001",
        "report_id": "r-HLTH-001",
        "artifact_count": 1,
        "entity_count": 0,
        "anomaly_count": 0,
        "critical_findings": [],
        "high_findings": [],
        "medium_findings": [],
        "low_findings": [],
        "info_findings": [],
        "master_timeline": [],
        "entity_directory": [],
        "audio_transcripts": [],
    }
    import canonicaljson

    (tmp_path / "HLTH-001_report.json").write_text(
        canonicaljson.encode_canonical_json(report).decode(), encoding="utf-8"
    )
    case_rag.index_case("HLTH-001")

    resp = client.get(
        "/api/v1/anchorum/cases/HLTH-001/index/health",
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["case_id"] == "HLTH-001"
    assert data["indexed"] is True
    assert data["chunks"] > 0
    assert data["store_size_bytes"] > 0
    assert data["query_latency_ms"] is not None


@pytest.mark.asyncio
async def test_chat_with_retries_on_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """_chat_with_retries should retry transient 500s and eventually succeed."""
    calls: list[int] = []

    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.text = "internal error"

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                import httpx

                raise httpx.HTTPStatusError(
                    "error", request=None, response=self  # type: ignore[arg-type]
                )

        def json(self) -> dict[str, Any]:
            return {"message": {"content": "ok after retry"}, "usage": {}}

    async def fake_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        calls.append(len(calls))
        if len(calls) < 3:
            return FakeResponse(500)
        return FakeResponse(200)

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)
    # Keep timeout short so the test is fast.
    monkeypatch.setattr(anchorum_http, "_CHAT_BACKOFF_SECONDS", 0.01)

    result = await anchorum_http._chat_with_retries(
        [{"role": "user", "content": "hi"}]
    )
    assert len(calls) == 3
    assert result["message"]["content"] == "ok after retry"
