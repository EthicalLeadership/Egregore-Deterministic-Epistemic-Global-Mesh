"""Tests for DOSS-03: UCSG Knowledge Graph."""

from __future__ import annotations

import pytest

from egregore.dossiers.DOSS_03_ucsg_knowledge.graph import (
    FactStatement,
    StatementType,
    UCSGKnowledgeGraph,
)


def test_graph_adds_fact():
    graph = UCSGKnowledgeGraph()
    fact = graph.add_fact("The sky is blue", "source-1")
    assert fact.content == "The sky is blue"
    assert fact.source_id == "source-1"
    assert fact.statement_type == StatementType.FACT


def test_graph_edges():
    graph = UCSGKnowledgeGraph()
    fact_a = graph.add_fact("The sky is blue", "source-1")
    fact_b = graph.add_fact("Blue is a color", "source-2")
    graph.add_edge(fact_a, fact_b)

    related = graph.related_facts(fact_a)
    assert len(related) == 1
    assert related[0].content == "Blue is a color"


def test_graph_edge_requires_existing_facts():
    graph = UCSGKnowledgeGraph()
    fact_a = FactStatement(content="orphan", source_id="s1")
    fact_b = graph.add_fact("Blue is a color", "source-2")
    with pytest.raises(ValueError, match="Both facts must be added"):
        graph.add_edge(fact_a, fact_b)


def test_graph_related_empty_for_unknown_fact():
    graph = UCSGKnowledgeGraph()
    orphan = FactStatement(content="orphan", source_id="s1")
    assert graph.related_facts(orphan) == []


def test_graph_normalizes_text():
    graph = UCSGKnowledgeGraph()
    normalized = graph.normalize("  Hello   World  ")
    assert normalized == "hello world"


def test_graph_detects_forbidden_phrase():
    graph = UCSGKnowledgeGraph()
    allowed, reason = graph.check_constraint(
        "This establishes liability for the defendant"
    )
    assert allowed is False
    assert "forbidden" in reason.lower()


def test_graph_allows_benign_text():
    graph = UCSGKnowledgeGraph()
    allowed, reason = graph.check_constraint("The weather is pleasant")
    assert allowed is True
    assert reason is None


def test_graph_queries_by_keyword():
    graph = UCSGKnowledgeGraph()
    graph.add_fact("The sky is blue", "source-1")
    graph.add_fact("Grass is green", "source-2")
    results = graph.query("blue")
    assert len(results) == 1
    assert results[0].content == "The sky is blue"


def test_graph_query_no_matches():
    graph = UCSGKnowledgeGraph()
    graph.add_fact("The sky is blue", "source-1")
    results = graph.query("nonexistent")
    assert results == []
