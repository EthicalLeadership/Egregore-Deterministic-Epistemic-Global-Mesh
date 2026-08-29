"""Tests for immutability layer."""

import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from egregore.domain.artifact.models import SecuredArtifact, AuthorizationToken, ImmutableArtifactError, ArtifactStatus
from egregore.domain.artifact.store import ArtifactStore

@pytest.fixture
def store(tmp_path):
    s = ArtifactStore(tmp_path / "artifacts")
    art = SecuredArtifact.from_content("doc1", "original content")
    s.save(art)
    return s

def test_secured_artifact_read_only(store):
    art = store.read("doc1")
    assert art.id == "doc1"
    assert art.content_hash != "original content"

def test_update_without_token_fails(store):
    with pytest.raises(ImmutableArtifactError):
        store.update("doc1", "new content")

def test_delete_without_token_fails(store):
    with pytest.raises(ImmutableArtifactError):
        store.delete("doc1")

def test_update_with_valid_token_succeeds(store):
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    token = AuthorizationToken("doc1", "update", "valid-token", exp, "human-admin")
    store.update("doc1", "new content", token)
    assert store.read("doc1").content_hash != "original content"

def test_update_with_expired_token_fails(store):
    exp = datetime.now(timezone.utc) - timedelta(seconds=1)
    token = AuthorizationToken("doc1", "update", "expired-token", exp, "human-admin")
    with pytest.raises(ImmutableArtifactError):
        store.update("doc1", "new content", token)

def test_delete_with_valid_token_succeeds(store):
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    token = AuthorizationToken("doc1", "delete", "valid-token", exp, "human-admin")
    store.delete("doc1", token)
    with pytest.raises(KeyError):
        store.read("doc1")
