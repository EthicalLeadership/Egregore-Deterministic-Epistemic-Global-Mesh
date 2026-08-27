"""Tests for Practice Standard Resolver."""

import pytest
from egregore.domain.practice.models import (
    Scope,
    Jurisdiction,
    Forum,
    DocumentType,
    Audience,
    PracticeStandard,
)
from egregore.domain.practice.resolver import resolve, list_standards


def test_resolve_quebec_superior_motion():
    scope = Scope(
        jurisdiction=Jurisdiction.QUEBEC,
        forum=Forum.SUPERIOR_COURT,
        document_type=DocumentType.MOTION,
        audience=Audience.COURT,
        purpose="seek interim injunction",
    )
    std = resolve(scope)
    assert std.id == "qc_superior_motion"
    assert std.jurisdiction == Jurisdiction.QUEBEC
    assert "conclusions" in std.required_sections
    assert std.citation_standard == "Guide des procédures civiles"
    assert std.exhibits is True


def test_resolve_ontario_motion():
    scope = Scope(
        jurisdiction=Jurisdiction.ONTARIO,
        forum=Forum.SUPERIOR_COURT,
        document_type=DocumentType.MOTION,
        audience=Audience.COURT,
        purpose="motion for summary judgment",
    )
    std = resolve(scope)
    assert std.id == "on_superior_motion"
    assert "factum" in std.required_sections
    assert std.citation_standard == "Canadian Guide to Uniform Legal Citation (McGill Guide)"


def test_resolve_internal_strategy_memo_falls_back():
    scope = Scope(
        jurisdiction=Jurisdiction.QUEBEC,
        forum=Forum.INTERNAL,
        document_type=DocumentType.STRATEGY_MEMO,
        audience=Audience.EXECUTIVE,
        purpose="business strategy",
    )
    std = resolve(scope)
    assert std.id == "internal_strategy_memo"
    assert std.required_sections[0] == "executive_summary"
    assert std.advocacy_level == "persuasive"


def test_resolve_diplomatic_briefing():
    scope = Scope(
        jurisdiction=Jurisdiction.FEDERAL_CANADA,
        forum=Forum.DIPLOMATIC_CHANNEL,
        document_type=DocumentType.DIPLOMATIC_BRIEFING,
        audience=Audience.DIPLOMATIC,
        purpose="bilateral trade negotiation",
    )
    std = resolve(scope)
    assert std.id == "diplomatic_briefing"
    assert std.tone == "diplomatic"
    assert "ultimatums" in std.prohibited_content


def test_resolve_unknown_scope_returns_generic():
    scope = Scope(
        jurisdiction=Jurisdiction.FEDERAL_CANADA,
        forum=Forum.ARBITRATION,
        document_type=DocumentType.OPINION,
        audience=Audience.CLIENT,
        purpose="legal opinion",
    )
    std = resolve(scope)
    assert std.id == "generic_legal"
    assert std.authority_standard == "secondary"
    assert std.formality == "high"


def test_registry_not_empty():
    assert len(list_standards()) >= 4
