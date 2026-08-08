"""Tests for the CLI agent runner."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import pytest

from egregore.application.agent_registry import AgentRegistry
from egregore.application.agent_runner import AgentRunner


def _caller_identity(*roles: str) -> dict[str, object]:
    return {
        "tenant_id": "default",
        "user_id": "u1",
        "username": "u1",
        "roles": list(roles),
        "status": "active",
    }


def _verify_token(_token: str) -> dict[str, object]:
    return _caller_identity("operator")


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

    result = runner.run(
        spec,
        '{"domain":"ops","required_privilege":"operator","output_sink":"chat","human_approval_required":true,"instruction":"hello"}',
        {
            "session_id": "s1",
            "role": "operator",
            "caller_identity_token": "test-token",
            "identity_verifier": _verify_token,
        },
    )

    assert result.ok is True
    assert result.returncode == 0
    assert '"instruction":"hello"' in result.stdout
    assert '"instruction":"hello"' in result.stderr


def test_runner_forwards_context_env(runner: AgentRunner, tmp_path: Path) -> None:
    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    agent_path = agent_dir / "context-agent"
    agent_path.write_text('#!/bin/sh\necho "$EGREGORE_AGENT_CONTEXT"\n')
    agent_path.chmod(agent_path.stat().st_mode | stat.S_IXUSR)

    registry = AgentRegistry(agent_dir)
    spec = registry.get("context-agent")
    assert spec is not None

    result = runner.run(
        spec,
        '{"domain":"ops","required_privilege":"operator","output_sink":"chat","human_approval_required":true,"instruction":"doit"}',
        {
            "session_id": "s1",
            "user_id": "u1",
            "role": "operator",
            "caller_identity_token": "test-token",
            "identity_verifier": _verify_token,
        },
    )
    assert result.ok is True
    assert '"session_id":"s1"' in result.stdout
    assert '"user_id":"u1"' in result.stdout
    assert '"task_intent":{' in result.stdout


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

    result = runner.run(
        spec,
        '{"domain":"ops","required_privilege":"operator","output_sink":"chat","human_approval_required":true,"instruction":"wait"}',
        {
            "role": "operator",
            "caller_identity_token": "test-token",
            "identity_verifier": _verify_token,
        },
    )
    assert result.ok is False
    assert result.timed_out is True


def test_runner_rejects_raw_string_without_legacy_flag(
    runner: AgentRunner, echo_agent: Path
) -> None:
    registry = AgentRegistry(echo_agent)
    spec = registry.get("echo-agent")
    assert spec is not None

    result = runner.run(
        spec,
        "hello",
        {
            "role": "operator",
            "caller_identity_token": "test-token",
            "identity_verifier": _verify_token,
        },
    )

    assert result.ok is False
    assert "TaskIntent must be valid JSON" in result.error


def test_runner_rejects_missing_caller_identity_token(
    runner: AgentRunner, echo_agent: Path
) -> None:
    registry = AgentRegistry(echo_agent)
    spec = registry.get("echo-agent")
    assert spec is not None

    result = runner.run(
        spec,
        '{"domain":"ops","required_privilege":"operator","output_sink":"chat","human_approval_required":true,"instruction":"hello"}',
        {"role": "operator"},
    )

    assert result.ok is False
    assert "caller_identity_token" in result.error


def test_chat_agent_command_requires_privilege() -> None:
    from egregore.application.chat_interpreter import ChatContext, execute_message

    env_payload: Any = {"agent_registry": AgentRegistry()}
    reader = ChatContext(
        session_id="s",
        user_id="u",
        role="reader",
        env=env_payload,
    )
    result = execute_message(
        '/agent example-agent {"domain":"ops","required_privilege":"operator","output_sink":"chat","human_approval_required":true,"instruction":"hello"}',
        reader,
    )
    assert result["ok"] is False
    assert "requires admin role" in result["summary"]


def test_chat_agents_command_lists_agents(tmp_path: Path) -> None:
    from egregore.application.chat_interpreter import ChatContext, execute_message

    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    agent_path = agent_dir / "listed-agent"
    agent_path.write_text("#!/bin/sh\necho ok\n")
    agent_path.chmod(agent_path.stat().st_mode | stat.S_IXUSR)

    env_payload: Any = {
        "agent_registry": AgentRegistry(agent_dir),
        "caller_identity_token": "test-token",
        "identity_verifier": _verify_token,
    }
    ctx = ChatContext(
        session_id="s",
        user_id="u",
        role="operator",
        env=env_payload,
    )
    result = execute_message("/agents", ctx)
    assert result["ok"] is True
    assert "listed-agent" in str(result["detail"]["agents"])


def test_chat_agent_rejects_free_form_instruction_by_default(tmp_path: Path) -> None:
    from egregore.application.chat_interpreter import ChatContext, execute_message

    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    agent_path = agent_dir / "json-only-agent"
    agent_path.write_text("#!/bin/sh\necho ok\n")
    agent_path.chmod(agent_path.stat().st_mode | stat.S_IXUSR)

    env_payload: Any = {
        "agent_registry": AgentRegistry(agent_dir),
        "caller_identity_token": "test-token",
        "identity_verifier": _verify_token,
    }
    ctx = ChatContext(
        session_id="s",
        user_id="u",
        role="operator",
        env=env_payload,
    )
    result = execute_message("/agent json-only-agent do this now", ctx)
    assert result["ok"] is False
    assert "TaskIntent must be valid JSON" in result["summary"]


def test_chat_agent_legacy_shim_requires_env_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from egregore.application.chat_interpreter import ChatContext, execute_message

    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    agent_path = agent_dir / "legacy-agent"
    agent_path.write_text("#!/bin/sh\necho ok\n")
    agent_path.chmod(agent_path.stat().st_mode | stat.S_IXUSR)

    env_payload: Any = {
        "agent_registry": AgentRegistry(agent_dir),
        "caller_identity_token": "test-token",
        "identity_verifier": _verify_token,
    }
    ctx = ChatContext(
        session_id="s",
        user_id="u",
        role="operator",
        env=env_payload,
    )
    monkeypatch.setenv("EGREGORE_AGENT_LEGACY_TASK_INTENT", "1")
    result = execute_message("/agent legacy-agent do this now", ctx)
    assert result["ok"] is True
    assert result["detail"]["warning"] == (
        "Legacy raw agent instructions are deprecated; send TaskIntent JSON."
    )


def test_chat_agent_accepts_task_intent_envelope(tmp_path: Path) -> None:
    from egregore.application.chat_interpreter import ChatContext, execute_message

    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    agent_path = agent_dir / "echo-intent-agent"
    agent_path.write_text('#!/bin/sh\necho "$1"\n')
    agent_path.chmod(agent_path.stat().st_mode | stat.S_IXUSR)

    env_payload: Any = {
        "agent_registry": AgentRegistry(agent_dir),
        "caller_identity_token": "test-token",
        "identity_verifier": _verify_token,
    }
    ctx = ChatContext(
        session_id="s",
        user_id="u",
        role="operator",
        env=env_payload,
    )
    cmd = (
        '/agent echo-intent-agent '
        '{"domain":"ops","required_privilege":"operator","output_sink":"chat",'
        '"human_approval_required":true,"instruction":"collect status"}'
    )
    result = execute_message(cmd, ctx)
    assert result["ok"] is True
    assert '"domain":"ops"' in result["summary"]
    assert '"instruction":"collect status"' in result["summary"]


def test_runner_rejects_insufficient_caller_identity_privilege(
    runner: AgentRunner, echo_agent: Path
) -> None:
    registry = AgentRegistry(echo_agent)
    spec = registry.get("echo-agent")
    assert spec is not None

    result = runner.run(
        spec,
        '{"domain":"ops","required_privilege":"admin","output_sink":"chat","human_approval_required":true,"instruction":"hello"}',
        {
            "role": "operator",
            "caller_identity_token": "test-token",
            "identity_verifier": _verify_token,
        },
    )

    assert result.ok is False
    assert "requires admin privilege" in result.error


def test_runner_rejects_removed_max_privilege_field(
    runner: AgentRunner, echo_agent: Path
) -> None:
    registry = AgentRegistry(echo_agent)
    spec = registry.get("echo-agent")
    assert spec is not None

    result = runner.run(
        spec,
        '{"domain":"ops","max_privilege":"operator","output_sink":"chat","human_approval_required":true,"instruction":"hello"}',
        {
            "role": "operator",
            "caller_identity_token": "test-token",
            "identity_verifier": _verify_token,
        },
    )

    assert result.ok is False
    assert "required_privilege" in result.error
