#!/usr/bin/env python3
"""
Fill stakeholder sign-off tables in ADR markdown files.

Usage:
    python3 signoff_adrs.py <adr_dir> <name> [date]

Example:
    python3 signoff_adrs.py ~/egregore/docs/adr "Kark" 2026-06-18
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path


def find_adrs(adr_dir: Path) -> list[Path]:
    """Return sorted ADR markdown files."""
    if not adr_dir.exists():
        raise FileNotFoundError(f"ADR directory not found: {adr_dir}")
    return sorted(p for p in adr_dir.iterdir() if p.is_file() and p.suffix == ".md")


def fill_signoffs(text: str, name: str, sign_date: str) -> str:
    """
    Replace empty stakeholder sign-off cells with the provided name and date.

    Expects tables under a '## Stakeholder Sign-off' heading with columns:
    Role | Name | Date | Status
    """
    roles = ["Architecture Lead", "Security Lead", "SRE Lead"]

    def replace_table(table: str) -> str:
        lines = table.splitlines()
        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("|") and not stripped.startswith("| Role") and "---" not in stripped:
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) >= 4:
                    role = cells[0]
                    if role in roles:
                        cells[1] = name
                        cells[2] = sign_date
                        if cells[3].lower() in ("", "pending"):
                            cells[3] = "Approved"
                        line = "| " + " | ".join(cells[:4]) + " |"
            new_lines.append(line)
        return "\n".join(new_lines)

    pattern = re.compile(
        r"## Stakeholder Sign-off\n\n(.*?)(?=\n## |\Z)",
        re.DOTALL,
    )
    return pattern.sub(lambda m: "## Stakeholder Sign-off\n\n" + replace_table(m.group(1)), text)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 1

    adr_dir = Path(sys.argv[1]).expanduser().resolve()
    name = sys.argv[2]
    sign_date = sys.argv[3] if len(sys.argv) > 3 else date.today().isoformat()

    adrs = find_adrs(adr_dir)
    if not adrs:
        print(f"No ADR markdown files found in {adr_dir}", file=sys.stderr)
        return 1

    for adr in adrs:
        text = adr.read_text(encoding="utf-8")
        new_text = fill_signoffs(text, name, sign_date)
        if new_text != text:
            adr.write_text(new_text, encoding="utf-8")
            print(f"Updated: {adr.name}")
        else:
            print(f"No changes: {adr.name}")

    print(f"\nProcessed {len(adrs)} ADR(s) with sign-off by {name} on {sign_date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
