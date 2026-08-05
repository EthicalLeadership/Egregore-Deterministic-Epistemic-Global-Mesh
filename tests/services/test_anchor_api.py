"""Tests for the anchor public verification API (fail-closed contract)."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from egregore.domain.anchor_record import AnchorRecord
from egregore.infrastructure.persistence.sqlite_anchor_store import SQLiteAnchorStore
from egregore.kernel.ed25519_signer import generate_signing_key
from egregore.services.anchor_orchestrator.api import create_anchor_router
from egregore.services.anchor_orchestrator.timestamp_client import (
    LocalFallbackTimestampClient,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_tsa_fixture():
    spec = importlib.util.spec_from_file_location(
        "tsa_fixture", REPO_ROOT / "tests" / "helpers" / "tsa_fixture.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _anchor_id(block_hash: str) -> str:
    return hashlib.sha256(f"anchor:{block_hash}".encode()).hexdigest()


def _app(store, trust_dir) -> TestClient:
    app = FastAPI()
    app.include_router(create_anchor_router(anchor_store=store, trust_dir=trust_dir))
    return TestClient(app)


def _store_record(store, *, tier, block_hash, notarization, anchor_id=None):
    record = AnchorRecord(
        anchor_id=anchor_id or _anchor_id(block_hash),
        tier=tier,
        block_hash=block_hash,
        notarization=notarization,
        public_verify=tier == "2",
        timestamp_ns=1000,
        metadata={},
    )
    store.append(record)
    return record


class TestTier2Verification:
    def test_valid_anchor_verifies(self, tmp_path: Path):
        fixture = _load_tsa_fixture()
        token, trust_dir, data_hash = fixture.make_tsa(tmp_path)
        store = SQLiteAnchorStore(str(tmp_path / "anchors.db"))
        record = _store_record(
            store, tier="2", block_hash=data_hash, notarization=token.hex()
        )
        client = _app(store, trust_dir)
        resp = client.get(f"/anchor/{record.anchor_id}/verify")
        assert resp.status_code == 200
        body = resp.json()
        assert body["verified"] is True
        assert body["tsa_report"]["verdict"] is True
        assert body["block_hash"] == data_hash

    def test_no_trust_dir_fails_closed(self, tmp_path: Path):
        fixture = _load_tsa_fixture()
        token, _, data_hash = fixture.make_tsa(tmp_path)
        store = SQLiteAnchorStore(str(tmp_path / "anchors.db"))
        record = _store_record(
            store, tier="2", block_hash=data_hash, notarization=token.hex()
        )
        client = _app(store, None)
        body = client.get(f"/anchor/{record.anchor_id}/verify").json()
        assert body["verified"] is False
        assert "trust anchor not configured" in body["reason"]

    def test_tampered_token_fails(self, tmp_path: Path):
        fixture = _load_tsa_fixture()
        token, trust_dir, data_hash = fixture.make_tsa(tmp_path)
        other_hash = hashlib.sha256(b"other").hexdigest()
        store = SQLiteAnchorStore(str(tmp_path / "anchors.db"))
        # Anchor claims a different block hash than the token stamps.
        record = _store_record(
            store, tier="2", block_hash=other_hash, notarization=token.hex()
        )
        client = _app(store, trust_dir)
        body = client.get(f"/anchor/{record.anchor_id}/verify").json()
        assert body["verified"] is False
        assert "reason" in body


class TestFailClosedPaths:
    def test_unknown_anchor_404(self, tmp_path: Path):
        store = SQLiteAnchorStore(str(tmp_path / "anchors.db"))
        client = _app(store, None)
        resp = client.get(f"/anchor/{'a' * 64}/verify")
        assert resp.status_code == 404
        assert resp.json()["detail"]["verified"] is False

    def test_invalid_anchor_id_400(self, tmp_path: Path):
        store = SQLiteAnchorStore(str(tmp_path / "anchors.db"))
        client = _app(store, None)
        assert client.get("/anchor/short/verify").status_code == 400

    def test_binding_mismatch_detected(self, tmp_path: Path):
        fixture = _load_tsa_fixture()
        token, trust_dir, data_hash = fixture.make_tsa(tmp_path)
        store = SQLiteAnchorStore(str(tmp_path / "anchors.db"))
        record = _store_record(
            store,
            tier="2",
            block_hash=data_hash,
            notarization=token.hex(),
            anchor_id="b" * 64,  # does not match sha256("anchor:"+block_hash)
        )
        client = _app(store, trust_dir)
        body = client.get(f"/anchor/{record.anchor_id}/verify").json()
        assert body["verified"] is False
        assert "binding mismatch" in body["reason"]

    def test_mock_tier_never_verifies(self, tmp_path: Path):
        store = SQLiteAnchorStore(str(tmp_path / "anchors.db"))
        record = _store_record(
            store, tier="0", block_hash="c" * 64, notarization="6d6f636b"
        )
        client = _app(store, None)
        body = client.get(f"/anchor/{record.anchor_id}/verify").json()
        assert body["verified"] is False


class TestLocalTier:
    def test_local_token_self_asserted(self, tmp_path: Path):
        key = generate_signing_key()
        block_hash = "d" * 64
        token = LocalFallbackTimestampClient(key).timestamp(block_hash)
        store = SQLiteAnchorStore(str(tmp_path / "anchors.db"))
        record = _store_record(
            store, tier="1", block_hash=block_hash, notarization=token.cms_bytes.hex()
        )
        client = _app(store, None)
        body = client.get(f"/anchor/{record.anchor_id}/verify").json()
        assert body["verified"] is False
        assert body["locally_verifiable"] is True
        assert "self-signed" in body["reason"]

    def test_local_token_tampered_hash(self, tmp_path: Path):
        key = generate_signing_key()
        token = LocalFallbackTimestampClient(key).timestamp("d" * 64)
        store = SQLiteAnchorStore(str(tmp_path / "anchors.db"))
        record = _store_record(
            store, tier="1", block_hash="e" * 64, notarization=token.cms_bytes.hex()
        )
        client = _app(store, None)
        body = client.get(f"/anchor/{record.anchor_id}/verify").json()
        assert body["verified"] is False
        assert body["locally_verifiable"] is False
