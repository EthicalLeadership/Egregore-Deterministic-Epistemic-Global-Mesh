# epistemic marker: governance / determinism
"""Governance DSL — deterministic expression-tree evaluator.

Pure domain module: no I/O, no wall-clock, no randomness. Expressions are
plain mappings (parsed from JSON/YAML rule files) compiled into a frozen
AST and evaluated against a context mapping.

Grammar::

    expr  := {"all": [expr, ...]}        # conjunction (>= 1 child)
           | {"any": [expr, ...]}        # disjunction (>= 1 child)
           | {"not": expr}               # negation
           | {"field": "<dotted.path>", <op>: <literal>}

    op    := eq | ne | gt | ge | lt | le | in | contains | matches

Semantics (fail-closed everywhere):
- parse errors (unknown op, malformed node, non-literal value) raise
  ``GovernanceDslError`` at parse time;
- evaluation errors (missing context field, strict type mismatch) raise
  ``GovernanceDslError`` at evaluation time;
- comparisons never coerce: numbers compare with numbers, strings with
  strings, bools only with bools; ``in`` requires a list literal,
  ``contains`` requires a list or string field, ``matches`` is a regex
  ``fullmatch`` against a string field.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class GovernanceDslError(Exception):
    """Fail-closed error for parse and evaluation violations."""


OPS = frozenset({"eq", "ne", "gt", "ge", "lt", "le", "in", "contains", "matches"})

_LITERAL_TYPES = (str, int, float, bool, type(None))


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cond:
    """Leaf condition: ``context[field] <op> value``."""

    field: str
    op: str
    value: Any


@dataclass(frozen=True)
class All:
    conditions: tuple[Any, ...]


@dataclass(frozen=True)
class AnyOf:
    conditions: tuple[Any, ...]


@dataclass(frozen=True)
class Not:
    condition: Any


# ---------------------------------------------------------------------------
# Parsing (fail-closed)
# ---------------------------------------------------------------------------


def _check_literal(value: Any) -> Any:
    if isinstance(value, _LITERAL_TYPES):
        return value
    if isinstance(value, list):
        for item in value:
            _check_literal(item)
        return value
    raise GovernanceDslError(
        f"Non-literal value in condition: {type(value).__name__} "
        "(literals are str/int/float/bool/null or lists of literals)"
    )


def parse_expr(node: Any) -> Any:
    """Compile a plain mapping into the frozen AST. Fail-closed."""
    if not isinstance(node, Mapping):
        raise GovernanceDslError(
            f"Expression node must be a mapping, got {type(node).__name__}"
        )
    keys = set(node.keys())

    if keys == {"all"}:
        children = node["all"]
        if not isinstance(children, Sequence) or isinstance(children, str) or not children:
            raise GovernanceDslError("'all' requires a non-empty list of expressions")
        return All(tuple(parse_expr(child) for child in children))

    if keys == {"any"}:
        children = node["any"]
        if not isinstance(children, Sequence) or isinstance(children, str) or not children:
            raise GovernanceDslError("'any' requires a non-empty list of expressions")
        return AnyOf(tuple(parse_expr(child) for child in children))

    if keys == {"not"}:
        return Not(parse_expr(node["not"]))

    if "field" in keys:
        field = node["field"]
        if not isinstance(field, str) or not field:
            raise GovernanceDslError("'field' must be a non-empty string")
        ops = keys - {"field"}
        if len(ops) != 1:
            raise GovernanceDslError(
                f"Leaf condition must have exactly one operator, got {sorted(ops)}"
            )
        op = next(iter(ops))
        if op not in OPS:
            raise GovernanceDslError(f"Unknown operator: {op!r}")
        return Cond(field=field, op=op, value=_check_literal(node[op]))

    raise GovernanceDslError(
        f"Malformed expression node with keys {sorted(keys)}; "
        "expected 'all', 'any', 'not', or 'field' + operator"
    )


# ---------------------------------------------------------------------------
# Evaluation (fail-closed, deterministic)
# ---------------------------------------------------------------------------


def _resolve_field(context: Mapping[str, Any], path: str) -> Any:
    current: Any = context
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise GovernanceDslError(f"Missing context field: {path!r}")
        current = current[part]
    return current


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _compare(op: str, actual: Any, expected: Any) -> bool:
    if op in ("eq", "ne"):
        # Strict: bools never equal numbers; type mismatch is inequality.
        if isinstance(actual, bool) != isinstance(expected, bool):
            result = False
        elif isinstance(actual, float) or isinstance(expected, float):
            result = _is_number(actual) and _is_number(expected) and float(actual) == float(expected)
        else:
            result = type(actual) is type(expected) and actual == expected
        return result if op == "eq" else not result

    if op in ("gt", "ge", "lt", "le"):
        if _is_number(actual) and _is_number(expected):
            pass
        elif isinstance(actual, str) and isinstance(expected, str):
            pass
        else:
            raise GovernanceDslError(
                f"Operator {op!r} requires both operands to be numbers or both "
                f"strings; got {type(actual).__name__} vs {type(expected).__name__}"
            )
        return {
            "gt": actual > expected,
            "ge": actual >= expected,
            "lt": actual < expected,
            "le": actual <= expected,
        }[op]

    if op == "in":
        if not isinstance(expected, list):
            raise GovernanceDslError("'in' requires a list literal")
        return any(_compare("eq", actual, item) for item in expected)

    if op == "contains":
        if isinstance(actual, str) and isinstance(expected, str):
            return expected in actual
        if isinstance(actual, Sequence) and not isinstance(actual, str):
            return any(_compare("eq", item, expected) for item in actual)
        raise GovernanceDslError(
            f"'contains' requires a list or string field, got {type(actual).__name__}"
        )

    if op == "matches":
        if not isinstance(actual, str) or not isinstance(expected, str):
            raise GovernanceDslError("'matches' requires string field and pattern")
        try:
            return re.fullmatch(expected, actual) is not None
        except re.error as exc:
            raise GovernanceDslError(f"Invalid regex pattern {expected!r}: {exc}") from exc

    raise GovernanceDslError(f"Unknown operator: {op!r}")  # unreachable post-parse


def evaluate(expr: Any, context: Mapping[str, Any]) -> bool:
    """Evaluate a compiled AST against ``context``. Fail-closed."""
    if isinstance(expr, Cond):
        actual = _resolve_field(context, expr.field)
        return _compare(expr.op, actual, expr.value)
    if isinstance(expr, All):
        return all(evaluate(child, context) for child in expr.conditions)
    if isinstance(expr, AnyOf):
        return any(evaluate(child, context) for child in expr.conditions)
    if isinstance(expr, Not):
        return not evaluate(expr.condition, context)
    raise GovernanceDslError(f"Unknown AST node: {type(expr).__name__}")
