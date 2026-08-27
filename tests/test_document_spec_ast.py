"""Tests for Document Specification and AST builder."""

import pytest
from egregore.domain.practice.models import (
    Scope,
    Jurisdiction,
    Forum,
    DocumentType,
    Audience,
)
from egregore.domain.practice.resolver import resolve
from egregore.domain.practice.document_spec import DocumentSpecification
from egregore.domain.practice.document_ast import NodeType
from egregore.domain.practice.document_builder import build_empty_ast


def test_spec_from_quebec_motion():
    scope = Scope(
        jurisdiction=Jurisdiction.QUEBEC,
        forum=Forum.SUPERIOR_COURT,
        document_type=DocumentType.MOTION,
        audience=Audience.COURT,
        purpose="interim injunction",
    )
    standard = resolve(scope)
    spec = DocumentSpecification.from_practice_standard(standard, scope)

    assert spec.id.startswith("doc-spec-qc_superior_motion")
    assert "conclusions" in spec.required_sections
    assert spec.citation_standard == "Guide des procédures civiles"
    assert spec.exhibits is True
    assert spec.formality == "high"
    assert "speculation" in spec.prohibited_content


def test_build_empty_ast_contains_required_sections_in_order():
    scope = Scope(
        jurisdiction=Jurisdiction.ONTARIO,
        forum=Forum.SUPERIOR_COURT,
        document_type=DocumentType.MOTION,
        audience=Audience.COURT,
        purpose="summary judgment",
    )
    standard = resolve(scope)
    spec = DocumentSpecification.from_practice_standard(standard, scope)
    ast = build_empty_ast(spec)

    # Root node
    assert ast.root.node_type == NodeType.ROOT

    # Headings should appear in the same order as required sections
    headings = [n for n in ast.root.children if n.node_type == NodeType.HEADING]
    assert [h.text for h in headings] == list(spec.required_sections)

    # There should be an empty paragraph after each heading
    paragraphs = [n for n in ast.root.children if n.node_type == NodeType.PARAGRAPH]
    assert len(paragraphs) == len(spec.required_sections)


def test_build_empty_ast_adds_exhibits_section_when_required():
    scope = Scope(
        jurisdiction=Jurisdiction.QUEBEC,
        forum=Forum.SUPERIOR_COURT,
        document_type=DocumentType.MOTION,
        audience=Audience.COURT,
        purpose="injunction",
    )
    standard = resolve(scope)
    spec = DocumentSpecification.from_practice_standard(standard, scope)
    assert spec.exhibits is True
    ast = build_empty_ast(spec)
    headings = [n.text for n in ast.root.children if n.node_type == NodeType.HEADING]
    assert "Exhibits" in headings
