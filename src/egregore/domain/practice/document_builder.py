"""Document Builder.

Builds an empty DocumentAST skeleton from a DocumentSpecification.
The structure is determined solely by the specification; the builder
does not make content decisions.
"""

from __future__ import annotations

from egregore.domain.practice.document_spec import DocumentSpecification
from egregore.domain.practice.document_ast import (
    DocumentAST,
    RootNode,
    HeadingNode,
    ParagraphNode,
    make_heading,
    make_empty_paragraph,
)


def build_empty_ast(spec: DocumentSpecification) -> DocumentAST:
    """Create an empty AST skeleton with required sections in order."""
    children = []
    for section in spec.required_sections:
        # Create a heading for the section (level 1)
        heading_id = f"heading-{section.lower().replace(' ', '_')}"
        children.append(make_heading(level=1, text=section, node_id=heading_id))
        # Add an empty paragraph placeholder
        children.append(make_empty_paragraph(section))

    # Add exhibits section if exhibits are required
    if spec.exhibits:
        children.append(make_heading(level=1, text="Exhibits"))
        children.append(make_empty_paragraph("exhibits"))

    root = RootNode(id="root", children=tuple(children))
    return DocumentAST(
        root=root,
        specification_id=spec.id,
        provenance={"builder": "empty_ast_v1"},
    )
