"""Tests for DOSS-04: Autonomous Response Engine."""

from __future__ import annotations

import pytest

from egregore.dossiers.DOSS_04_autonomous_response.engine import (
    AutonomousAuthority,
    AutonomousResponseEngine,
    ExecutionBlock,
)


def test_block_executes_and_creates_audit_log():
    block = ExecutionBlock(action="read", target="documents")
    result = block.execute()
    assert result["status"] == "executed"
    assert block.audit_log["action"] == "read"
    assert "audit_id" in block.audit_log
    assert "timestamp" in block.audit_log
    # Timestamp should be a real Unix timestamp (float), not a hash string
    assert isinstance(block.audit_log["timestamp"], float)
    assert block.audit_log["timestamp"] > 0


def test_engine_allows_authorized_execution():
    engine = AutonomousResponseEngine(required_role="admin")
    authority = AutonomousAuthority(user_id="admin-1", roles=["admin"])
    block = ExecutionBlock(action="delete", target="records")
    result = engine.execute(authority, block)
    assert result["status"] == "executed"


def test_engine_blocks_unauthorized_execution():
    engine = AutonomousResponseEngine(required_role="admin")
    authority = AutonomousAuthority(user_id="user-1", roles=["viewer"])
    block = ExecutionBlock(action="delete", target="records")
    with pytest.raises(PermissionError):
        engine.execute(authority, block)


def test_engine_no_required_role_allows_any():
    engine = AutonomousResponseEngine(required_role=None)
    authority = AutonomousAuthority(user_id="guest", roles=[])
    block = ExecutionBlock(action="read", target="public")
    result = engine.execute(authority, block)
    assert result["status"] == "executed"


def test_engine_tracks_history():
    engine = AutonomousResponseEngine(required_role="admin")
    authority = AutonomousAuthority(user_id="admin-1", roles=["admin"])
    block1 = ExecutionBlock(action="read", target="docs")
    block2 = ExecutionBlock(action="write", target="config")
    engine.execute(authority, block1)
    engine.execute(authority, block2)
    history = engine.get_history()
    assert len(history) == 2


def test_engine_history_is_copy():
    engine = AutonomousResponseEngine(required_role="admin")
    authority = AutonomousAuthority(user_id="admin-1", roles=["admin"])
    engine.execute(authority, ExecutionBlock(action="read", target="docs"))
    history = engine.get_history()
    history.clear()
    # Original history should be unaffected
    assert len(engine.get_history()) == 1
