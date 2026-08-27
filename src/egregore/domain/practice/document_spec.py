"""Document Specification.

A DocumentSpecification is the resolved production profile for a
specific Scope. It is derived from a PracticeStandard and includes
the original scope for context. The document compiler uses this
object to determine structure, formatting, and content constraints.
"""

from __future__ import annotations

from typing import FrozenSet, Tuple, Optional, Any

from pydantic import BaseModel, ConfigDict, Field

from egregore.domain.practice.models import Scope, PracticeStandard


class DocumentSpecification(BaseModel):
    """Immutable specification for a document to be produced."""

    model_config = ConfigDict(frozen=True)

    id: str
    standard: PracticeStandard
    scope: Scope

    required_sections: Tuple[str, ...]
    optional_sections: Tuple[str, ...] = ()
    prohibited_content: Tuple[str, ...] = ()

    citation_standard: str
    authority_standard: str
    tone: str
    formality: str
    advocacy_level: str

    formatting: FrozenSet[str] = frozenset()
    pagination: bool = True
    exhibits: bool = False
    attachments: bool = False

    @classmethod
    def from_practice_standard(cls, standard: PracticeStandard, scope: Scope) -> "DocumentSpecification":
        """Construct a specification from a practice standard and scope."""
        required = list(standard.required_sections)
        if standard.exhibits and "Exhibits" not in required:
            required.append("Exhibits")

        return cls(
            id=f"doc-spec-{standard.id}-{scope.jurisdiction.value}-{scope.document_type.value}",
            standard=standard,
            scope=scope,
            required_sections=tuple(required),
            optional_sections=standard.optional_sections,
            prohibited_content=standard.prohibited_content,
            citation_standard=standard.citation_standard,
            authority_standard=standard.authority_standard,
            tone=standard.tone,
            formality=standard.formality,
            advocacy_level=standard.advocacy_level,
            formatting=standard.formatting,
            pagination=standard.pagination,
            exhibits=standard.exhibits,
            attachments=standard.attachments,
        )
