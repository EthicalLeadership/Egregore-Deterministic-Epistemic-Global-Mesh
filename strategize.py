cd ~/egregore
.venv/bin/python - <<'PY'
from pathlib import Path

content = '''"""CLI entrypoint for `anchorum strategize`."""

from __future__ import annotations

import argparse

from egregore.application.strategy.engine import StrategyEngine
from egregore.application.strategy.memo_builder import render_memo
from egregore.application.strategy.models import StrategyScope


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="anchorum strategize",
        description="Generate 360° elite strategy memo for a dossier.",
    )
    parser.add_argument("dossier_id", help="Dossier/case ID (e.g. MOLSON-2026)")
    parser.add_argument("--goals", required=True, help="Strategic goals")
    parser.add_argument("--jurisdiction", default="QC", help="Jurisdiction code")
    parser.add_argument("--constraints", nargs="*", default=[], help="Constraint labels")
    parser.add_argument("--stakeholders", nargs="*", default=[], help="Stakeholder labels")
    parser.add_argument("--evidence", nargs="*", default=[], help="Evidence IDs")
    args = parser.parse_args()

    scope = StrategyScope(
        matter_id=args.dossier_id,
        jurisdiction=args.jurisdiction,
        goals=args.goals,
        constraints=tuple(args.constraints),
        stakeholders=tuple(args.stakeholders),
        evidence_refs=tuple(args.evidence),
    )

    engine = StrategyEngine()
    memo = engine.run(scope)
    print(render_memo(memo))


if __name__ == "__main__":
    main()
'''

Path("src/egregore/cli/strategize.py").write_text(content)
print("strategize.py written")
PY
