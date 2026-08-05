"""Tests for the governance expression-tree DSL and rule-set loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from egregore.application.governance_policy_loader import (
    GovernancePolicyError,
    GovernanceRuleSet,
    load_rule_set,
    parse_rule_set,
    register_rule_set,
)
from egregore.application.policy_versioning import (
    InMemoryPolicyVersionRegistry,
    VersionedPolicyExecutor,
)
from egregore.domain.governance_dsl import (
    All,
    AnyOf,
    Cond,
    GovernanceDslError,
    Not,
    evaluate,
    parse_expr,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "config" / "governance_rules.example.json"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class TestParse:
    def test_leaf(self):
        expr = parse_expr({"field": "a.b", "ge": 3})
        assert expr == Cond(field="a.b", op="ge", value=3)

    def test_combinators(self):
        expr = parse_expr(
            {"all": [{"any": [{"field": "x", "eq": 1}, {"field": "y", "eq": 2}]},
                     {"not": {"field": "z", "eq": True}}]}
        )
        assert isinstance(expr, All)
        assert isinstance(expr.conditions[0], AnyOf)
        assert isinstance(expr.conditions[1], Not)

    def test_unknown_operator_rejected(self):
        with pytest.raises(GovernanceDslError, match="Unknown operator"):
            parse_expr({"field": "x", "explode": 1})

    def test_two_operators_rejected(self):
        with pytest.raises(GovernanceDslError, match="exactly one operator"):
            parse_expr({"field": "x", "eq": 1, "ne": 2})

    def test_empty_all_rejected(self):
        with pytest.raises(GovernanceDslError, match="non-empty list"):
            parse_expr({"all": []})

    def test_non_literal_rejected(self):
        with pytest.raises(GovernanceDslError, match="Non-literal"):
            parse_expr({"field": "x", "eq": {"nested": "dict"}})

    def test_non_mapping_rejected(self):
        with pytest.raises(GovernanceDslError, match="must be a mapping"):
            parse_expr(["not", "a", "mapping"])

    def test_malformed_node_rejected(self):
        with pytest.raises(GovernanceDslError, match="Malformed expression"):
            parse_expr({"whenever": True})


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_numeric_comparisons(self):
        ctx = {"qc": {"confidence": 0.9}}
        assert evaluate(parse_expr({"field": "qc.confidence", "ge": 0.85}), ctx)
        assert not evaluate(parse_expr({"field": "qc.confidence", "lt": 0.85}), ctx)

    def test_missing_field_fails_closed(self):
        with pytest.raises(GovernanceDslError, match="Missing context field"):
            evaluate(parse_expr({"field": "nope", "eq": 1}), {})

    def test_strict_type_comparison_fails_closed(self):
        with pytest.raises(GovernanceDslError, match="numbers or both"):
            evaluate(parse_expr({"field": "x", "gt": 1}), {"x": "string"})

    def test_bool_never_equals_number(self):
        assert not evaluate(parse_expr({"field": "x", "eq": 1}), {"x": True})
        assert evaluate(parse_expr({"field": "x", "ne": 1}), {"x": True})

    def test_in_and_contains(self):
        ctx = {"tags": ["a", "b"], "name": "egregore"}
        assert evaluate(parse_expr({"field": "tags", "contains": "a"}), ctx)
        assert evaluate(parse_expr({"field": "name", "contains": "greg"}), ctx)
        assert evaluate(parse_expr({"field": "name", "in": ["x", "egregore"]}), ctx)

    def test_matches_fullmatch(self):
        ctx = {"role": "operator"}
        assert evaluate(parse_expr({"field": "role", "matches": "(operator|admin)"}), ctx)
        assert not evaluate(parse_expr({"field": "role", "matches": "op"}), ctx)

    def test_boolean_logic(self):
        ctx = {"a": 1, "b": 2}
        expr = parse_expr({"all": [{"field": "a", "eq": 1},
                                   {"not": {"field": "b", "eq": 3}}]})
        assert evaluate(expr, ctx)

    def test_determinism(self):
        expr = parse_expr({"any": [{"field": "x", "ge": 5}, {"field": "x", "le": 1}]})
        ctx = {"x": 7}
        assert all(evaluate(expr, ctx) for _ in range(50))


# ---------------------------------------------------------------------------
# Rule sets
# ---------------------------------------------------------------------------


def _document() -> dict:
    return {
        "version": "1.0.0",
        "rules": [
            {"id": "deny-bad-tenant",
             "when": {"field": "tenant", "eq": "evil"},
             "then": {"verdict": "deny", "reason": "bad tenant"}},
            {"id": "escalate-low-qc",
             "when": {"field": "qc.confidence", "lt": 0.5},
             "then": {"verdict": "require_escalation", "reason": "low qc"}},
            {"id": "allow-standard",
             "when": {"field": "qc.confidence", "ge": 0.5},
             "then": {"verdict": "allow", "reason": "standard"}},
        ],
    }


class TestRuleSet:
    def test_deny_wins(self):
        rules = parse_rule_set(_document())
        result = rules.compute({"tenant": "evil", "qc": {"confidence": 0.9}})
        assert result["verdict"] == "deny"
        assert set(result["matched_rule_ids"]) == {"deny-bad-tenant", "allow-standard"}

    def test_escalation_beats_allow(self):
        rules = parse_rule_set(_document())
        result = rules.compute({"tenant": "acme", "qc": {"confidence": 0.3}})
        assert result["verdict"] == "require_escalation"

    def test_allow(self):
        rules = parse_rule_set(_document())
        result = rules.compute({"tenant": "acme", "qc": {"confidence": 0.9}})
        assert result["verdict"] == "allow"
        assert result["reason"] == "standard"

    def test_default_deny_when_no_match(self):
        rules = parse_rule_set(
            {"version": "1", "rules": [
                {"id": "r", "when": {"field": "x", "eq": 1},
                 "then": {"verdict": "allow", "reason": ""}}
            ]}
        )
        result = rules.compute({"x": 2})
        assert result["verdict"] == "deny"
        assert "fail-closed" in result["reason"]

    def test_validate_rejects_non_mapping(self):
        rules = parse_rule_set(_document())
        with pytest.raises(ValueError):
            rules.compute(["not", "a", "mapping"])

    def test_malformed_documents_fail_closed(self):
        with pytest.raises(GovernancePolicyError):
            parse_rule_set({"rules": []})
        with pytest.raises(GovernancePolicyError, match="Duplicate rule id"):
            parse_rule_set({"version": "1", "rules": [
                {"id": "r", "when": {"field": "x", "eq": 1},
                 "then": {"verdict": "allow", "reason": ""}},
                {"id": "r", "when": {"field": "x", "eq": 2},
                 "then": {"verdict": "deny", "reason": ""}},
            ]})
        with pytest.raises(GovernancePolicyError, match="invalid verdict"):
            parse_rule_set({"version": "1", "rules": [
                {"id": "r", "when": {"field": "x", "eq": 1},
                 "then": {"verdict": "maybe", "reason": ""}},
            ]})
        with pytest.raises(GovernancePolicyError, match="invalid 'when'"):
            parse_rule_set({"version": "1", "rules": [
                {"id": "r", "when": {"field": "x", "explode": 1},
                 "then": {"verdict": "allow", "reason": ""}},
            ]})

    def test_policy_hash_stable(self):
        first = parse_rule_set(_document())
        second = parse_rule_set(_document())
        assert first.policy_hash == second.policy_hash
        changed = _document()
        changed["rules"][0]["then"]["reason"] = "different"
        assert parse_rule_set(changed).policy_hash != first.policy_hash


class TestLoading:
    def test_example_file_loads(self):
        rule_set = load_rule_set(EXAMPLE)
        assert isinstance(rule_set, GovernanceRuleSet)
        assert rule_set.version == "1.0.0"
        assert len(rule_set.rules) == 4

    def test_example_semantics(self):
        rule_set = load_rule_set(EXAMPLE)
        denied = rule_set.compute(
            {"tenant": "restricted", "qc": {"confidence": 0.99, "tier": "standard"},
             "budget": {"post_balance": 5}, "role": "admin"}
        )
        assert denied["verdict"] == "deny"
        escalated = rule_set.compute(
            {"tenant": "acme", "qc": {"confidence": 0.5, "tier": "critical"},
             "budget": {"post_balance": 5}, "role": "operator"}
        )
        assert escalated["verdict"] == "require_escalation"
        allowed = rule_set.compute(
            {"tenant": "acme", "qc": {"confidence": 0.9, "tier": "standard"},
             "budget": {"post_balance": 5}, "role": "admin"}
        )
        assert allowed["verdict"] == "allow"

    def test_yaml_roundtrip(self, tmp_path: Path):
        yaml_file = tmp_path / "rules.yaml"
        yaml_file.write_text(
            'version: "2.0.0"\n'
            "rules:\n"
            "  - id: r1\n"
            "    when: {field: x, eq: 1}\n"
            "    then: {verdict: allow, reason: ok}\n"
        )
        rule_set = load_rule_set(yaml_file)
        assert rule_set.compute({"x": 1})["verdict"] == "allow"
        assert rule_set.compute({"x": 2})["verdict"] == "deny"

    def test_missing_file_fails_closed(self, tmp_path: Path):
        with pytest.raises(GovernancePolicyError, match="not found"):
            load_rule_set(tmp_path / "absent.json")

    def test_unparseable_file_fails_closed(self, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        with pytest.raises(GovernancePolicyError, match="Cannot parse"):
            load_rule_set(bad)


class TestVersionedIntegration:
    def test_registry_executor_roundtrip(self):
        rule_set = load_rule_set(EXAMPLE)
        registry = InMemoryPolicyVersionRegistry()
        register_rule_set(registry, rule_set)
        assert registry.current_version() == "1.0.0"

        executor = VersionedPolicyExecutor(registry=registry)
        command = {
            "tenant": "acme",
            "qc": {"confidence": 0.9, "tier": "standard"},
            "budget": {"post_balance": 5},
            "role": "admin",
        }
        first = executor.execute(
            command=command, engine_version="1.0.0", policy_version="1.0.0"
        )
        second = executor.execute(
            command=command, engine_version="1.0.0", policy_version="1.0.0"
        )
        assert first.policy_result["verdict"] == "allow"
        # Deterministic replay: identical inputs → identical result mapping.
        assert dict(first.policy_result) == dict(second.policy_result)

    def test_unknown_version_rejected(self):
        registry = InMemoryPolicyVersionRegistry()
        executor = VersionedPolicyExecutor(registry=registry)
        with pytest.raises(ValueError, match="not found"):
            executor.execute(command={}, engine_version="1", policy_version="nope")
