"""Builds a final strategy memo from analyses."""

from __future__ import annotations

from egregore.application.strategy.models import StrategyMemo


def render_memo(memo: StrategyMemo) -> str:
    """Render a StrategyMemo into a concise text document."""
    lines = [
        f"STRATEGY MEMO: {memo.matter_id}",
        f"Status: {memo.status}",
        f"Summary: {memo.summary}",
        "",
    ]
    for analysis in memo.analyses:
        lines.append(
            f"## {analysis.perspective.value.upper()} / {analysis.horizon.value.upper()}"
        )
        lines.extend(f"- Finding: {f}" for f in analysis.findings)
        lines.extend(f"- Risk: {r}" for r in analysis.risks)
        lines.extend(f"- Option: {o}" for o in analysis.options)
        lines.append("")
    return "\n".join(lines)
