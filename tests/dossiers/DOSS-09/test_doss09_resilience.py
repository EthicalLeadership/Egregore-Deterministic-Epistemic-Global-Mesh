"""Tests for DOSS-09: Federation & Resilience."""

from __future__ import annotations

import pytest

from egregore.dossiers.DOSS_09_federation_resilience.resilience import (
    FederationResilience,
    InMemoryTreatyStore,
    TreatyState,
)


def test_treaty_proposal():
    store = InMemoryTreatyStore()
    resilience = FederationResilience(store=store)
    treaty = resilience.propose("treaty-1", ["pioneer-1", "pioneer-2"], ["mutual-aid"])
    assert treaty.state == TreatyState.PROPOSED
    assert treaty.treaty_id == "treaty-1"


def test_treaty_ratification_requires_all_parties():
    store = InMemoryTreatyStore()
    resilience = FederationResilience(store=store)
    resilience.propose("treaty-1", ["pioneer-1", "pioneer-2"], ["mutual-aid"])

    # Only one signature
    result = resilience.ratify("treaty-1", "pioneer-1", "sig-1")
    assert result.state == TreatyState.PROPOSED  # Not active yet

    # Second signature activates
    result = resilience.ratify("treaty-1", "pioneer-2", "sig-2")
    assert result.state == TreatyState.ACTIVE


def test_treaty_non_party_cannot_ratify():
    store = InMemoryTreatyStore()
    resilience = FederationResilience(store=store)
    resilience.propose("treaty-1", ["pioneer-1", "pioneer-2"], ["mutual-aid"])

    with pytest.raises(ValueError, match="not a party"):
        resilience.ratify("treaty-1", "pioneer-3", "sig-3")


def test_treaty_ratify_missing_treaty():
    store = InMemoryTreatyStore()
    resilience = FederationResilience(store=store)
    result = resilience.ratify("nonexistent", "pioneer-1", "sig-1")
    assert result is None


def test_active_treaty_retrieval():
    store = InMemoryTreatyStore()
    resilience = FederationResilience(store=store)
    resilience.propose("treaty-1", ["pioneer-1", "pioneer-2"], ["mutual-aid"])
    resilience.ratify("treaty-1", "pioneer-1", "sig-1")
    resilience.ratify("treaty-1", "pioneer-2", "sig-2")

    active = resilience.active_treaty()
    assert active is not None
    assert active.treaty_id == "treaty-1"


def test_active_treaties_multiple():
    store = InMemoryTreatyStore()
    resilience = FederationResilience(store=store)
    resilience.propose("treaty-1", ["pioneer-1", "pioneer-2"], ["mutual-aid"])
    resilience.ratify("treaty-1", "pioneer-1", "sig-1")
    resilience.ratify("treaty-1", "pioneer-2", "sig-2")

    resilience.propose("treaty-2", ["pioneer-1", "pioneer-3"], ["data-sharing"])
    resilience.ratify("treaty-2", "pioneer-1", "sig-a")
    resilience.ratify("treaty-2", "pioneer-3", "sig-b")

    all_active = resilience.active_treaties()
    assert len(all_active) == 2


def test_has_ratified():
    store = InMemoryTreatyStore()
    resilience = FederationResilience(store=store)
    resilience.propose("treaty-1", ["pioneer-1", "pioneer-2"], ["mutual-aid"])
    resilience.ratify("treaty-1", "pioneer-1", "sig-1")
    resilience.ratify("treaty-1", "pioneer-2", "sig-2")

    assert resilience.has_ratified("pioneer-1", "treaty-1") is True
    assert resilience.has_ratified("pioneer-2", "treaty-1") is True
    assert resilience.has_ratified("pioneer-3", "treaty-1") is False


def test_has_ratified_not_active():
    store = InMemoryTreatyStore()
    resilience = FederationResilience(store=store)
    resilience.propose("treaty-1", ["pioneer-1", "pioneer-2"], ["mutual-aid"])
    resilience.ratify("treaty-1", "pioneer-1", "sig-1")
    # Only one signature — treaty is still PROPOSED, not ACTIVE
    assert resilience.has_ratified("pioneer-1", "treaty-1") is False
