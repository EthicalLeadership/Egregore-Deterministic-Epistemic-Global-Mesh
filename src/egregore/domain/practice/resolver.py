"""Practice Standard Resolver.

Deterministically resolves a given Scope to the correct PracticeStandard.
The mapping is rule‑based, not learned, and follows a clear fallback chain.

The resolver uses a registry of predefined standards and matches on:
    jurisdiction + document type + forum + audience
in decreasing specificity. If no exact match is found, it falls back to
a default standard for the jurisdiction and document type, and finally
to a generic legal standard.
"""

from __future__ import annotations

from typing import Dict, Tuple, Optional, Any, FrozenSet
from .models import (
    Scope,
    PracticeStandard,
    Jurisdiction,
    Forum,
    DocumentType,
    Audience,
)

# Registry of predefined standards keyed by (jurisdiction, document_type, forum, audience)
_REGISTRY: Dict[Tuple[Jurisdiction, DocumentType, Forum, Audience], PracticeStandard] = {}

def _register(std: PracticeStandard) -> None:
    key = (std.jurisdiction, std.document_type, std.forum, std.audience)
    _REGISTRY[key] = std


# ---------------------------------------------------------------------------
# Predefined Standards
# ---------------------------------------------------------------------------

# Quebec Superior Court Motion
_register(
    PracticeStandard(
        id="qc_superior_motion",
        jurisdiction=Jurisdiction.QUEBEC,
        forum=Forum.SUPERIOR_COURT,
        document_type=DocumentType.MOTION,
        audience=Audience.COURT,
        required_sections=("style_of_cause", "title", "parties", "conclusions", "allegations", "affidavit", "exhibits", "signature"),
        optional_sections=("authorities",),
        prohibited_content=("speculation", "unsupported_legal_conclusions"),
        citation_standard="Guide des procédures civiles",
        authority_standard="primary",
        tone="formal",
        formality="high",
        advocacy_level="controlled",
        formatting=frozenset({"double_spaced", "12pt", "numbered_paragraphs"}),
        pagination=True,
        exhibits=True,
        attachments=False,
    )
)

# Ontario Superior Court Motion
_register(
    PracticeStandard(
        id="on_superior_motion",
        jurisdiction=Jurisdiction.ONTARIO,
        forum=Forum.SUPERIOR_COURT,
        document_type=DocumentType.MOTION,
        audience=Audience.COURT,
        required_sections=("title", "parties", "notice_of_motion", "affidavit", "factum", "certificate", "signature"),
        optional_sections=("book_of_authorities",),
        prohibited_content=("speculation", "unsupported_legal_conclusions"),
        citation_standard="Canadian Guide to Uniform Legal Citation (McGill Guide)",
        authority_standard="primary",
        tone="formal",
        formality="high",
        advocacy_level="controlled",
        formatting=frozenset({"double_spaced", "12pt", "numbered_paragraphs"}),
        pagination=True,
        exhibits=True,
        attachments=False,
    )
)

# Quebec Court of Appeal Brief
_register(
    PracticeStandard(
        id="qc_appellate_brief",
        jurisdiction=Jurisdiction.QUEBEC,
        forum=Forum.COURT_OF_APPEAL,
        document_type=DocumentType.BRIEF,
        audience=Audience.COURT,
        required_sections=("cover_page", "table_of_contents", "table_of_authorities", "issues", "statement_of_facts", "argument", "conclusion", "signature"),
        optional_sections=("appendices",),
        prohibited_content=("new_evidence", "speculation"),
        citation_standard="Guide des procédures civiles",
        authority_standard="primary",
        tone="formal",
        formality="maximum",
        advocacy_level="controlled",
        formatting=frozenset({"double_spaced", "12pt", "numbered_paragraphs", "bound"}),
        pagination=True,
        exhibits=True,
        attachments=True,
    )
)

# Internal business strategy memo (generic)
_register(
    PracticeStandard(
        id="internal_strategy_memo",
        jurisdiction=Jurisdiction.FEDERAL_CANADA,
        forum=Forum.INTERNAL,
        document_type=DocumentType.STRATEGY_MEMO,
        audience=Audience.EXECUTIVE,
        required_sections=("executive_summary", "background", "analysis", "options", "recommendation"),
        optional_sections=("appendices",),
        prohibited_content=("legal_conclusions",),
        citation_standard="none",
        authority_standard="none",
        tone="concise",
        formality="medium",
        advocacy_level="persuasive",
        formatting=frozenset({"single_spaced", "11pt"}),
        pagination=True,
        exhibits=False,
        attachments=True,
    )
)

# Diplomatic briefing (generic)
_register(
    PracticeStandard(
        id="diplomatic_briefing",
        jurisdiction=Jurisdiction.FEDERAL_CANADA,
        forum=Forum.DIPLOMATIC_CHANNEL,
        document_type=DocumentType.DIPLOMATIC_BRIEFING,
        audience=Audience.DIPLOMATIC,
        required_sections=("summary", "context", "interests", "positions", "options", "recommendation"),
        optional_sections=("annexes",),
        prohibited_content=("legal_conclusions", "ultimatums"),
        citation_standard="none",
        authority_standard="none",
        tone="diplomatic",
        formality="high",
        advocacy_level="restrained",
        formatting=frozenset({"single_spaced", "11pt"}),
        pagination=True,
        exhibits=False,
        attachments=True,
    )
)


def resolve(scope: Scope) -> PracticeStandard:
    """Resolve a Scope to a PracticeStandard.

    Lookup order:
        1. Exact match on (jurisdiction, document_type, forum, audience)
        2. Match on (jurisdiction, document_type, forum) with audience ignored
        3. Match on (jurisdiction, document_type) with forum/audience ignored
        4. Match on (document_type) with jurisdiction/forum/audience ignored
        5. Generic legal standard (if no match)
    """
    # 1. Exact
    key = (scope.jurisdiction, scope.document_type, scope.forum, scope.audience)
    if key in _REGISTRY:
        return _REGISTRY[key]

    # 2. Jurisdiction + document_type + forum
    for (j, dt, f, a), std in _REGISTRY.items():
        if j == scope.jurisdiction and dt == scope.document_type and f == scope.forum:
            return std

    # 3. Jurisdiction + document_type
    for (j, dt, f, a), std in _REGISTRY.items():
        if j == scope.jurisdiction and dt == scope.document_type:
            return std

    # 4. Document_type only
    for (j, dt, f, a), std in _REGISTRY.items():
        if dt == scope.document_type:
            return std

    # 5. Generic legal standard (default)
    return PracticeStandard(
        id="generic_legal",
        jurisdiction=scope.jurisdiction,
        forum=scope.forum,
        document_type=scope.document_type,
        audience=scope.audience,
        required_sections=("title", "body", "conclusion"),
        citation_standard="McGill Guide",
        authority_standard="secondary",
        tone="formal",
        formality="high",
        advocacy_level="controlled",
        formatting=frozenset({"double_spaced", "12pt"}),
        pagination=True,
        exhibits=False,
        attachments=False,
    )


def list_standards() -> Tuple[PracticeStandard, ...]:
    """Return all registered standards."""
    return tuple(_REGISTRY.values())
