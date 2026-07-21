"""Tests for the CLI agent registry."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from egregore.application.agent_registry import AgentRegistry


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    return tmp_path / "agents"


def _make_executable(path: Path) -> None:
    path.write_text("#!/bin/sh\necho ok\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_registry_discovers_executable_files(agent_dir: Path) -> None:
    agent_dir.mkdir()
    _make_executable(agent_dir / "alpha-agent")
    _make_executable(agent_dir / "beta-agent")
    (agent_dir / "not-executable.txt").write_text("skip me")

    registry = AgentRegistry(agent_dir)
    names = [a.name for a in registry.list_agents()]
    assert "alpha-agent" in names
    assert "beta-agent" in names
    assert "not-executable.txt" not in names


def test_registry_reads_manifest(agent_dir: Path) -> None:
    agent_dir.mkdir()
    _make_executable(agent_dir / "described-agent")
    (agent_dir / "described-agent.json").write_text(
        '{"description": "A test agent", "timeout": 30, "allowed_roles": ["admin"]}'
    )

    registry = AgentRegistry(agent_dir)
    spec = registry.get("described-agent")
    assert spec is not None
    assert spec.description == "A test agent"
    assert spec.timeout == 30.0
    assert spec.allowed_roles == {"admin"}


def test_registry_empty_when_directory_missing(agent_dir: Path) -> None:
    registry = AgentRegistry(agent_dir)
    assert registry.list_agents() == []


def test_registry_uses_env_override(agent_dir: Path, monkeypatch) -> None:
    agent_dir.mkdir()
    _make_executable(agent_dir / "env-agent")
    monkeypatch.setenv("BLACKSTAR_AGENTS_DIR", str(agent_dir))

    registry = AgentRegistry()
    assert registry.get("env-agent") is not None
