"""Document AST.

A structured representation of a document. Nodes are immutable and
each node has a unique identifier for traceability and cross-reference
validation. The compiler fills content into these nodes; it does not
create new structure beyond the specification.
"""

from __future__ import annotations

from typing import Union, Tuple, List, Optional, Any
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class NodeType(str, Enum):
    ROOT = "root"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    CITATION = "citation"
    FOOTNOTE = "footnote"
    EXHIBIT_REF = "exhibit_ref"
    CROSS_REF = "cross_ref"


class DocumentNode(BaseModel):
    """Base class for all AST nodes."""
    model_config = ConfigDict(frozen=True)

    id: str
    node_type: NodeType


class HeadingNode(DocumentNode):
    node_type: NodeType = NodeType.HEADING
    level: int = Field(ge=1, le=6)
    text: str


class CitationNode(DocumentNode):
    node_type: NodeType = NodeType.CITATION
    source_id: str
    pinpoint: Optional[str] = None


class FootnoteNode(DocumentNode):
    node_type: NodeType = NodeType.FOOTNOTE
    text: str
    citations: Tuple[CitationNode, ...] = ()


class ExhibitRefNode(DocumentNode):
    node_type: NodeType = NodeType.EXHIBIT_REF
    exhibit_id: str
    description: str = ""


class CrossReferenceNode(DocumentNode):
    node_type: NodeType = NodeType.CROSS_REF
    target_id: str
    text: str = ""


class ParagraphNode(DocumentNode):
    node_type: NodeType = NodeType.PARAGRAPH
    text: str = ""
    citations: Tuple[CitationNode, ...] = ()
    footnotes: Tuple[FootnoteNode, ...] = ()
    children: Tuple["DocumentNode", ...] = ()


class RootNode(DocumentNode):
    node_type: NodeType = NodeType.ROOT
    children: Tuple[DocumentNode, ...] = ()


class DocumentAST(BaseModel):
    """The complete abstract syntax tree of a document."""
    model_config = ConfigDict(frozen=True)

    root: RootNode
    specification_id: str
    provenance: dict[str, str] = Field(default_factory=dict)


def make_heading(level: int, text: str, node_id: Optional[str] = None) -> HeadingNode:
    """Factory for heading nodes."""
    return HeadingNode(
        id=node_id or f"heading-{level}-{text.lower().replace(' ', '_')}",
        level=level,
        text=text,
    )


def make_empty_paragraph(section: str) -> ParagraphNode:
    """Factory for an empty paragraph placeholder."""
    return ParagraphNode(
        id=f"paragraph-empty-{section.lower().replace(' ', '_')}",
        text="",
        citations=(),
        footnotes=(),
    )
