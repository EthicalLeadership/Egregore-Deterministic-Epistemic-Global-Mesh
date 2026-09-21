#!/usr/bin/env python3
"""Run the Quebec-law kill test against the live ANCHORUM chat endpoint.

Usage: .venv/bin/python scripts/legal_kill_test.py [--model MODEL] [--url URL]
Prints each question, the model's answer, and a simple keyword scorecard.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

QUESTIONS = [
    (
        "Q1 employment/notice",
        "Under Quebec law, an employee of 6 years is terminated without cause. "
        "What is the maximum indemnity in lieu of notice they can claim, and "
        "what is the governing article of the Code civil du Québec?",
        {"pass": ["2091"], "bonus": ["24 months"], "fail": ["Bardal", "reasonable notice factors only"]},
    ),
    (
        "Q2 SAAQ/insurance",
        "A Quebec worker injured in a car accident receives SAAQ income "
        "replacement benefits. They also have private short-term disability "
        "through their employer. Does SAAQ automatically reduce its benefits "
        "because private STD is being paid? Cite the law.",
        {"pass": ["A-25"], "bonus": ["primary", "90%"], "fail": ["dollar-for-dollar", "federal"]},
    ),
    (
        "Q3 CNESST reprisal",
        "A worker files a CNESST claim. The employer transfers them to night "
        "shift two weeks later. What is the legal presumption under the Act "
        "respecting industrial accidents and occupational diseases, and who "
        "bears the burden of proof?",
        {"pass": ["255"], "bonus": ["art. 32", "employer"], "fail": ["at-will", "right to work"]},
    ),
    (
        "Q0 constructive dismissal (regression)",
        "An employee in Quebec has worked 8 years, salary $72k. Employer "
        "unilaterally cuts salary by 18% and relocates office from Montreal "
        "to Laval (commute +45 min). Employee resigns and claims constructive "
        "dismissal. What is the precise legal test under Quebec law, what "
        "damages are available, and what is the prescription period? Cite "
        "the exact article of the Code civil du Québec.",
        {"pass": ["Farber", "2091"], "bonus": ["2925", "45"], "fail": ["60 days", "Bardal"]},
    ),
]

REFUSAL_MARKERS = ["not equipped", "I recommend consulting", "cannot provide", "I'm sorry"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8080/api/v1/anchorum/chat")
    ap.add_argument("--model-note", default="")
    args = ap.parse_args()

    key = (Path(__file__).resolve().parents[1] / "secrets" / "api_key.hex").read_text().strip()
    failures = 0
    for name, question, marks in QUESTIONS:
        resp = requests.post(
            args.url,
            headers={"X-API-Key": key},
            json={"message": question, "mode": "legal"},
            timeout=300,
        )
        data = resp.json()
        content = data.get("content", "")
        print("=" * 78)
        print(f"{name}  (model: {data.get('model', '?')})")
        print("-" * 78)
        print(content)
        refused = any(mk in content for mk in REFUSAL_MARKERS)
        hits = [p for p in marks["pass"] if p in content]
        misses = [f for f in marks["fail"] if f in content]
        verdict = "REFUSAL" if refused else ("PASS" if hits and not misses else "WEAK/FAIL")
        if verdict != "PASS":
            failures += 1
        print(f"\n>>> {verdict} | pass-markers found: {hits} | fail-markers found: {misses}")
    print("=" * 78)
    print(f"Failures: {failures}/{len(QUESTIONS)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
