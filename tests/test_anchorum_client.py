"""Focused tests for the Egregore-to-ANCHORUM adapter contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from egregore.tools.anchorum.client import AnchorumClient, ANCHORUM_TOOLS


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AnchorumClient:
    key_path = tmp_path / "egregore.key"
    key_path.write_text("service-key", encoding="utf-8")
    monkeypatch.setenv("ANCHORUM_EGREGORE_KEY_PATH", str(key_path))
    return AnchorumClient(base_url="https://anchorum.invalid", api_key="service-key")


def test_client_exposes_read_tools_by_default() -> None:
    names = {tool["function"]["name"] for tool in ANCHORUM_TOOLS}
    assert "anchorum.list_cases" in names
    assert "anchorum.get_timeline" in names
    assert "anchorum.reindex_case" not in names


def test_write_tools_are_denied_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    result = client.reindex_case("MOLSON-2026", "request-1")
    assert result.ok is False
    assert result.error_code == "forbidden"
    assert result.request_id == "request-1"


def test_case_arguments_are_validated_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="invalid characters"):
        client.get_timeline("../escape")
