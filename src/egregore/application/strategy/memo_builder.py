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

    # Matches the parenthesized list of IDs after "facts "
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

        # Show up to 3 findings, truncating fact lists
        findings = analysis.findings[:3]
        for f in findings:
            lines.append(f"- Finding: {_truncate_fact_list(f)}")

        # Show up to 2 risks
        for r in analysis.risks[:2]:
            lines.append(f"- Risk: {r}")

        # Show up to 2 options, truncating fact lists
        for o in analysis.options[:2]:
            lines.append(f"- Option: {_truncate_fact_list(o)}")

        lines.append("")

    return "\n".join(lines)
