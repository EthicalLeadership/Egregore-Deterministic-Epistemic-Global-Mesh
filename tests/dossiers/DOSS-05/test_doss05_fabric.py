"""Tests for DOSS-05: Identity & Trust Fabric."""

from __future__ import annotations

from egregore.dossiers.DOSS_05_identity_trust.fabric import (
    Article,
    Constitution,
    PermissionRegistry,
)


def test_constitution_article_lookup():
    constitution = Constitution(
        version="1.0",
        ratified_by=("pioneer-1",),
        articles=(
            Article(id="art-1", title="Mutual Aid", obligations=("help",)),
            Article(id="art-2", title="Non-Aggression", obligations=("no-attack",)),
        ),
        required_treaty_clauses=("mutual-aid",),
    )
    article = constitution.article("art-1")
    assert article is not None
    assert article.title == "Mutual Aid"
    missing = constitution.article("art-99")
    assert missing is None


def test_constitution_required_clauses():
    constitution = Constitution(
        version="1.0",
        ratified_by=("pioneer-1",),
        articles=(),
        required_treaty_clauses=("clause-a", "clause-b"),
    )
    clauses = constitution.required_clauses()
    assert "clause-a" in clauses
    assert "clause-b" in clauses


def test_permission_registry_grants_and_revokes():
    registry = PermissionRegistry()
    registry.grant("user-1", "read")
    registry.grant("user-1", "write")
    assert registry.has("user-1", "read")
    assert registry.has("user-1", "write")
    assert not registry.has("user-1", "admin")
    registry.revoke("user-1", "write")
    assert not registry.has("user-1", "write")


def test_permission_registry_lists_actions():
    registry = PermissionRegistry()
    registry.grant("user-1", "write")
    registry.grant("user-1", "read")
    actions = registry.list_actions("user-1")
    assert actions == ["read", "write"]


def test_permission_registry_empty_user():
    registry = PermissionRegistry()
    assert not registry.has("nonexistent", "read")
    assert registry.list_actions("nonexistent") == []


def test_permission_registry_revoke_nonexistent():
    # Should not raise
    registry = PermissionRegistry()
    registry.revoke("user-1", "admin")
    assert not registry.has("user-1", "admin")
