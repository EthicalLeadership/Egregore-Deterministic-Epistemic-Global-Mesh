"""Tests for DOSS-06: Threat Intelligence Fusion."""

from __future__ import annotations

from egregore.dossiers.DOSS_06_threat_intelligence.fusion import (
    CrossSignMessage,
    InMemoryTrustMeshStore,
    ThreatIntelligenceFusion,
)


def test_fusion_detects_invalid_signature():
    store = InMemoryTrustMeshStore()
    fusion = ThreatIntelligenceFusion(
        node_id="pioneer-1",
        trust_store=store,
        ban_threshold=3,
        suspect_threshold=1,
    )
    msg = CrossSignMessage(
        node_id="bad-node",
        payload_hash="hash-1",
        signature="invalid-sig",
        timestamp_ns=1,
        public_key_fingerprint="bad-fp",
    )
    valid = fusion.verify(msg)
    assert valid is False

    state = store.get("bad-node")
    assert state is not None
    assert state.violation_count == 1
    assert state.status == "SUSPECT"


def test_fusion_accepts_valid_signature():
    store = InMemoryTrustMeshStore()
    fusion = ThreatIntelligenceFusion(
        node_id="pioneer-1",
        trust_store=store,
        ban_threshold=3,
        suspect_threshold=1,
    )
    msg = CrossSignMessage(
        node_id="good-node",
        payload_hash="hash-1",
        signature="sig:valid-1",
        timestamp_ns=1,
        public_key_fingerprint="fp-1",
    )
    valid = fusion.verify(msg)
    assert valid is True

    state = store.get("good-node")
    assert state is not None
    assert state.status == "HEALTHY"


def test_fusion_bans_after_threshold():
    store = InMemoryTrustMeshStore()
    fusion = ThreatIntelligenceFusion(
        node_id="pioneer-1",
        trust_store=store,
        ban_threshold=3,
        suspect_threshold=1,
    )
    for i in range(3):
        msg = CrossSignMessage(
            node_id="byzantine",
            payload_hash=f"hash-{i}",
            signature="invalid",
            timestamp_ns=i,
            public_key_fingerprint="bad",
        )
        fusion.verify(msg)

    banned = fusion.malicious_nodes()
    assert "byzantine" in banned


def test_fusion_recovers_trust_score():
    store = InMemoryTrustMeshStore()
    fusion = ThreatIntelligenceFusion(
        node_id="pioneer-1",
        trust_store=store,
        ban_threshold=3,
        suspect_threshold=1,
    )
    # One bad signature
    fusion.verify(
        CrossSignMessage(
            node_id="rehab",
            payload_hash="h1",
            signature="bad",
            timestamp_ns=1,
            public_key_fingerprint="fp",
        )
    )
    # Many good signatures
    for i in range(20):
        fusion.verify(
            CrossSignMessage(
                node_id="rehab",
                payload_hash=f"h{i}",
                signature="sig:good",
                timestamp_ns=2 + i,
                public_key_fingerprint="fp",
            )
        )

    state = store.get("rehab")
    assert state.trust_score > 0.5
    assert state.status == "HEALTHY"


def test_fusion_unknown_node_starts_healthy():
    store = InMemoryTrustMeshStore()
    fusion = ThreatIntelligenceFusion(
        node_id="pioneer-1",
        trust_store=store,
        ban_threshold=3,
        suspect_threshold=1,
    )
    msg = CrossSignMessage(
        node_id="new-node",
        payload_hash="h1",
        signature="sig:ok",
        timestamp_ns=1,
        public_key_fingerprint="fp",
    )
    fusion.verify(msg)
    state = store.get("new-node")
    assert state.trust_score == 1.0
    assert state.status == "HEALTHY"


def test_fusion_no_malicious_nodes_initially():
    store = InMemoryTrustMeshStore()
    fusion = ThreatIntelligenceFusion(
        node_id="pioneer-1",
        trust_store=store,
        ban_threshold=3,
        suspect_threshold=1,
    )
    assert fusion.malicious_nodes() == []
