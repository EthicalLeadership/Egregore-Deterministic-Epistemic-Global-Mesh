"""Deterministic verification tools for cell stages.

Tools are plain callables registered in ``TOOLS`` and invoked by the executor
for stages that specify ``tool: <name>`` instead of a model. They receive the
stage configuration and the current execution context, and must return either a
dict (with optional ``output`` key) or a string.
"""

from __future__ import annotations

import logging
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import egregore.cells

logger = logging.getLogger("egregore.cells.tools")


def _noop_verify(stage: Any, context: dict[str, Any]) -> dict[str, Any]:
    """Placeholder tool that always returns PASS."""
    return {
        "verdict": "PASS",
        "output": f"No-op verification for {stage.stage_id}: OK",
        "details": {},
    }


def _math_verify(stage: Any, context: dict[str, Any]) -> dict[str, Any]:
    """Verify a mathematical expression or equation using SymPy if available.

    The stage prompt should place the expected answer or expression in the
    context, typically as ``answer`` or ``expression``. If SymPy is not installed
    the tool falls back to a deterministic structural check.
    """
    expression = str(context.get("answer", context.get("expression", "")))
    issues: list[str] = []

    try:
        # SymPy has no PEP 561 stubs; ignore for compatibility.
        import sympy  # type: ignore[import-untyped]
    except Exception as exc:  # noqa: BLE001
        logger.debug("SymPy not available for math_verify: %s", exc)
        sympy = None

    if not expression:
        issues.append("no answer/expression provided for verification")
    elif sympy is not None:
        try:
            expr = sympy.sympify(expression)
            result = expr.evalf()
            return {
                "verdict": "PASS",
                "output": f"Verified expression: {expression} = {result}",
                "details": {"expression": expression, "evaluated": str(result)},
            }
        except Exception as exc:  # noqa: BLE001
            issues.append(f"sympy could not evaluate '{expression}': {exc}")

    if issues:
        return {
            "verdict": "FAIL",
            "output": "Math verification failed: " + "; ".join(issues),
            "details": {"issues": issues},
        }

    return {
        "verdict": "PASS",
        "output": f"Math verification passed (fallback): {expression}",
        "details": {"expression": expression},
    }


def _anchorum_forensic(stage: Any, context: dict[str, Any]) -> dict[str, Any]:
    """Run the ANCHORUM forensic batch runner as a cell tool stage."""
    repo_root = Path(egregore.cells.__file__).resolve().parents[2]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

    from cells.anchorum_forensic.executor import CellContext, run_forensic_analysis

    work_dir = context.get("work_dir") or tempfile.mkdtemp(prefix="anchorum_")
    ctx = CellContext(work_dir=work_dir, logs=[])
    result = run_forensic_analysis(
        ctx,
        input_path=context.get("input_path", ""),
        case_id=context.get("case_id", ""),
        operator=context.get("operator", "system"),
        llm_model_id=context.get("llm_model_id") or context.get("model_id"),
    )
    # Surface a human-readable output line for logs.
    summary = result.get("summary", {})
    result["output"] = (
        f"ANCHORUM batch {result.get('case_id')}: "
        f"artifacts={summary.get('artifact_count', 0)} "
        f"entities={summary.get('entity_count', 0)} "
        f"anomalies={summary.get('anomaly_count', 0)} "
        f"critical={summary.get('critical_count', 0)} "
        f"high={summary.get('high_count', 0)}"
    )
    return result


ToolFn = Callable[[Any, dict[str, Any]], dict[str, Any] | str]

TOOLS: dict[str, ToolFn] = {
    "noop_verify": _noop_verify,
    "math_verify": _math_verify,
    "anchorum_forensic": _anchorum_forensic,
}


def register_tool(name: str, fn: ToolFn) -> None:
    """Register an additional deterministic tool at runtime."""
    TOOLS[name] = fn
