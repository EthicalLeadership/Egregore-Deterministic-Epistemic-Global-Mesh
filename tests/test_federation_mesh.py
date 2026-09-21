"""Tests for the SEL-X federation mesh."""

from __future__ import annotations

from egregore.application.federation_mesh import FederationMesh, InMemoryTrustMeshStore
from egregore.infrastructure.ed25519_signing_backend import Ed25519SigningBackend
from egregore.kernel.ed25519_signer import generate_signing_key


def _mesh() -> FederationMesh:
    key = generate_signing_key()
    return FederationMesh(
        node_id="node-a",
        signing_backend=Ed25519SigningBackend(key),
        trust_store=InMemoryTrustMeshStore(),
    )


def test_cross_sign_produces_valid_message() -> None:
    mesh = _mesh()
    payload_hash = "a" * 64
    msg = mesh.cross_sign(payload_hash)
    assert msg.node_id == "node-a"
    assert msg.payload_hash == payload_hash
    assert len(msg.signature) > 0
    assert len(msg.public_key_fingerprint) > 0


def test_verify_valid_cross_sign() -> None:
    mesh = _mesh()
    payload_hash = "a" * 64
    msg = mesh.cross_sign(payload_hash)
    assert mesh.verify_cross_sign(msg) is True


def test_verify_invalid_signature() -> None:
    mesh = _mesh()
    msg = mesh.cross_sign("a" * 64)
    # Tamper with payload hash.
    from dataclasses import replace

    bad_msg = replace(msg, payload_hash="b" * 64)
    assert mesh.verify_cross_sign(bad_msg) is False


def test_malicious_node_detection_bans_after_threshold() -> None:
    mesh = _mesh()
    for _ in range(3):
        msg = mesh.cross_sign("a" * 64)
        from dataclasses import replace

        bad = replace(msg, node_id="bad-node", payload_hash="b" * 64)
        mesh.verify_cross_sign(bad)
    assert "bad-node" in mesh.malicious_node_detection()


def test_trust_state_tracks_violations() -> None:
    mesh = _mesh()
    msg = mesh.cross_sign("a" * 64)
    from dataclasses import replace

    bad = replace(msg, node_id="peer", payload_hash="b" * 64)
    mesh.verify_cross_sign(bad)
    state = mesh.trust_mesh()[0]
    assert state.node_id == "peer"
    assert state.violation_count == 1
    assert state.status == "SUSPECT"
