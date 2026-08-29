"""Builds a final strategy memo from analyses."""

from __future__ import annotations

import re

from egregore.application.strategy.models import StrategyMemo


def _truncate_fact_list(text: str) -> str:
    """Replace long parenthesized fact-ID lists with a concise count."""
    def repl(match):
        ids = re.findall(r"'([a-f0-9]{16,})'", match.group(0))
        if len(ids) <= 5:
            return match.group(0)
        return f"[{len(ids)} fact IDs]"

    return re.sub(r"\([^)]*\)", repl, text)


def render_memo(memo: StrategyMemo) -> str:
    """Render a concise executive strategy memo."""
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
        for f in analysis.findings[:3]:
            lines.append(f"- Finding: {_truncate_fact_list(f)}")
        for r in analysis.risks[:2]:
            lines.append(f"- Risk: {r}")
        for o in analysis.options[:2]:
            lines.append(f"- Option: {_truncate_fact_list(o)}")
        lines.append("")

    return "\n".join(lines)
