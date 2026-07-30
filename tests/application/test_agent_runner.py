"""Tests for the CLI agent runner."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from egregore.application.agent_registry import AgentRegistry
from egregore.application.agent_runner import AgentRunner


@pytest.fixture
def runner() -> AgentRunner:
    return AgentRunner()


@pytest.fixture
def echo_agent(tmp_path: Path) -> Path:
    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    agent_path = agent_dir / "echo-agent"
    agent_path.write_text('#!/bin/sh\necho "stdout:$1"\necho "stderr:$1" >&2\n')
    agent_path.chmod(agent_path.stat().st_mode | stat.S_IXUSR)
    return agent_dir


def test_runner_captures_stdout_and_stderr(
    runner: AgentRunner, echo_agent: Path
) -> None:
    registry = AgentRegistry(echo_agent)
    spec = registry.get("echo-agent")
    assert spec is not None

    result = runner.run(spec, "hello", {"session_id": "s1"})

    assert result.ok is True
    assert result.returncode == 0
    assert "stdout:hello" in result.stdout
    assert "stderr:hello" in result.stderr


def test_runner_forwards_context_env(runner: AgentRunner, tmp_path: Path) -> None:
    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    agent_path = agent_dir / "context-agent"
    agent_path.write_text('#!/bin/sh\necho "$EGREGORE_AGENT_CONTEXT"\n')
    agent_path.chmod(agent_path.stat().st_mode | stat.S_IXUSR)

    registry = AgentRegistry(agent_dir)
    spec = registry.get("context-agent")
    assert spec is not None

    result = runner.run(spec, "doit", {"session_id": "s1", "user_id": "u1"})
    assert result.ok is True
    assert '"session_id":"s1"' in result.stdout
    assert '"user_id":"u1"' in result.stdout


def test_runner_enforces_timeout(runner: AgentRunner, tmp_path: Path) -> None:
    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    agent_path = agent_dir / "slow-agent"
    agent_path.write_text("#!/bin/sh\nsleep 10\n")
    agent_path.chmod(agent_path.stat().st_mode | stat.S_IXUSR)
    (agent_dir / "slow-agent.json").write_text('{"timeout": 0.5}')

    registry = AgentRegistry(agent_dir)
    spec = registry.get("slow-agent")
    assert spec is not None

    result = runner.run(spec, "wait", {})
    assert result.ok is False
    assert result.timed_out is True


def test_chat_agent_command_requires_privilege() -> None:
    from egregore.application.chat_interpreter import ChatContext, execute_message

    reader = ChatContext(
        session_id="s",
        user_id="u",
        role="reader",
        env={"agent_registry": AgentRegistry()},
    )
    result = execute_message("/agent example-agent hello", reader)
    assert result["ok"] is False
    assert "requires admin role" in result["summary"]


def test_chat_agents_command_lists_agents(tmp_path: Path) -> None:
    from egregore.application.chat_interpreter import ChatContext, execute_message

    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    agent_path = agent_dir / "listed-agent"
    agent_path.write_text("#!/bin/sh\necho ok\n")
    agent_path.chmod(agent_path.stat().st_mode | stat.S_IXUSR)

    ctx = ChatContext(
        session_id="s",
        user_id="u",
        role="operator",
        env={"agent_registry": AgentRegistry(agent_dir)},
    )
    result = execute_message("/agents", ctx)
    assert result["ok"] is True
    assert "listed-agent" in str(result["detail"]["agents"])
