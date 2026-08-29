"""Tests for read-only agent accessor."""

import pytest
from pathlib import Path
from egregore.domain.artifact.store import ArtifactStore
from egregore.domain.artifact.models import SecuredArtifact, ImmutableArtifactError
from egregore.domain.artifact.access import AgentArtifactAccessor

@pytest.fixture
def accessor(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    art = SecuredArtifact.from_content("doc1", "hello world")
    store.save(art)
    return AgentArtifactAccessor(store), store

def test_accessor_has_read_only_methods(accessor):
    access, _ = accessor
    assert hasattr(access, "read")
    assert hasattr(access, "verify_content")
    assert not hasattr(access, "update")
    assert not hasattr(access, "delete")
    assert not hasattr(access, "save")

def test_accessor_read_works(accessor):
    access, _ = accessor
    art = access.read("doc1")
    assert art.id == "doc1"
    assert access.verify_content("doc1", "hello world") is True

def test_accessor_cannot_mutate(accessor):
    access, store = accessor
    # Attempt to mutate via accessor would raise AttributeError
    with pytest.raises(AttributeError):
        access.update("doc1", "new")
    # Store still enforces immutability
    with pytest.raises(ImmutableArtifactError):
        store.update("doc1", "new")
