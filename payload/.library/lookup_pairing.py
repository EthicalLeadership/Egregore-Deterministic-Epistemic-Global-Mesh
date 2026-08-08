#!/usr/bin/env python3
"""Lookup tool for primary-source enrichment pairings.

Parses the markdown tables in this directory and returns matching rows as JSON.

Examples:
    python lookup_pairing.py --textbook physics_halliday_resnick_walker --chapter 13
    python lookup_pairing.py --textbook biology_campbell --topic "DNA structure"
    python lookup_pairing.py --textbook physics_halliday_resnick_walker --list
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

LIBRARY_DIR = Path(__file__).resolve().parent

TEXTBOOK_FILES = {
    "physics_halliday_resnick_walker": "primary_pairings_physics_halliday_resnick_walker.md",
    "physics_griffiths_quantum_mechanics": "primary_pairings_physics_griffiths_quantum_mechanics.md",
    "biology_campbell": "primary_pairings_biology_campbell.md",
    "chemistry_mcmurry": "primary_pairings_chemistry_mcmurry.md",
}


def _clean_cell(text: str) -> str:
    """Strip markdown formatting and whitespace from a table cell."""
    text = text.strip()
    # Remove bold/italic markers
    text = re.sub(r"\*\*", "", text)
    text = re.sub(r"__", "", text)
    text = re.sub(r"\*(?!\*)", "", text)
    text = re.sub(r"_(?!_)", "", text)
    # Remove markdown links, keeping link text
    text = re.sub(r"\[(([^\]]+))\]\([^)]+\)", r"\1", text)
    # Remove inline code backticks
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Collapse whitespace
    text = " ".join(text.split())
    return text


def _parse_markdown_table(path: Path) -> list[dict[str, str]]:
    """Parse a markdown file and extract the first table's rows."""
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()

    # Find the first table separator line |---|---|
    table_start = -1
    for i, line in enumerate(lines):
        if re.match(r"^\|[-:\s|]+\|$", line):
            table_start = i
            break

    if table_start < 1:
        return []

    header_line = lines[table_start - 1]
    headers = [_clean_cell(cell) for cell in header_line.strip("|").split("|")]
    rows: list[dict[str, str]] = []

    for line in lines[table_start + 1 :]:
        if not line.startswith("|"):
            break
        cells = [_clean_cell(cell) for cell in line.strip("|").split("|")]
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))

    return rows


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _load_table(textbook_key: str) -> list[dict[str, str]]:
    filename = TEXTBOOK_FILES.get(textbook_key)
    if not filename:
        raise ValueError(
            f"Unknown textbook '{textbook_key}'. "
            f"Known: {', '.join(TEXTBOOK_FILES)}"
        )
    path = LIBRARY_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Pairings file not found: {path}")
    return _parse_markdown_table(path)


def lookup(
    textbook: str,
    chapter: str | None = None,
    topic: str | None = None,
) -> list[dict[str, str]]:
    """Return matching rows for a textbook."""
    rows = _load_table(textbook)
    matches: list[dict[str, str]] = []

    chapter_key = "Textbook Chapter (HRW)"
    if textbook == "physics_griffiths_quantum_mechanics":
        chapter_key = "Textbook Chapter (Griffiths)"
    elif textbook == "biology_campbell":
        chapter_key = "Textbook Chapter (Campbell)"
    elif textbook == "chemistry_mcmurry":
        chapter_key = "Textbook Chapter (McMurry)"

    for row in rows:
        if chapter:
            chapter_text = _normalize(row.get(chapter_key, ""))
            chapter_norm = _normalize(chapter)
            # If chapter looks like a number, match only at the start
            # (e.g. "5" matches "5. Force and Motion" but not "15. Oscillations").
            if chapter_norm.isdigit():
                if not re.match(
                    rf"^{re.escape(chapter_norm)}(\.\s|$)", chapter_text
                ):
                    continue
            else:
                if chapter_norm not in chapter_text:
                    continue
        if topic:
            topic_norm = _normalize(topic)
            searchable = " ".join(_normalize(v) for v in row.values())
            if topic_norm not in searchable:
                continue
        matches.append(row)

    return matches


def list_textbooks() -> dict[str, Any]:
    """Return a summary of all available textbooks and their row counts."""
    result: dict[str, Any] = {}
    for key, filename in TEXTBOOK_FILES.items():
        path = LIBRARY_DIR / filename
        if path.exists():
            rows = _parse_markdown_table(path)
            result[key] = {"file": filename, "pairings": len(rows)}
        else:
            result[key] = {"file": filename, "pairings": 0, "error": "missing"}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Look up primary-source enrichment pairings."
    )
    parser.add_argument(
        "--textbook",
        choices=list(TEXTBOOK_FILES),
        help="Textbook key to query. Required unless --list is used.",
    )
    parser.add_argument(
        "--chapter",
        help="Chapter number or chapter title substring to match.",
    )
    parser.add_argument(
        "--topic",
        help="Topic substring to search across all columns.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available textbooks instead of querying.",
    )

    args = parser.parse_args()

    if args.list:
        print(json.dumps(list_textbooks(), indent=2))
        return 0

    if not args.textbook:
        parser.error("--textbook is required unless --list is used")

    try:
        matches = lookup(args.textbook, chapter=args.chapter, topic=args.topic)
    except (ValueError, FileNotFoundError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps(matches, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
