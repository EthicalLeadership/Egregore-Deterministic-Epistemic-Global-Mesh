"""Headless tests for entity_timeline pure logic.

Covers timestamp coercion (including decimal numeric strings), entity-field
detection ranking, and event normalisation. Tk widgets are intentionally not
exercised here — there is no display in CI/sandbox; the canvas widget itself
is verified against the running desktop app.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from timeline_kernel import (  # noqa: E402
    ENTITY_FIELD_RANKING,
    detect_entity,
    detect_label,
    detect_timestamp,
    format_timestamp,
    normalize_events,
    parse_timestamp,
)

EPOCH_SECONDS = 1_700_000_000.0


# ----------------------------------------------------------------- timestamps
def test_parse_timestamp_decimal_string_is_seconds() -> None:
    # Regression: "1700000000.25" must parse as epoch seconds, not be dropped.
    assert parse_timestamp("1700000000.25") == pytest.approx(1_700_000_000.25)


def test_parse_timestamp_bare_integer_string_is_seconds() -> None:
    assert parse_timestamp("1700000000") == pytest.approx(EPOCH_SECONDS)


def test_parse_timestamp_numeric_types() -> None:
    assert parse_timestamp(1_700_000_000) == pytest.approx(EPOCH_SECONDS)
    assert parse_timestamp(1_700_000_000.25) == pytest.approx(1_700_000_000.25)
    assert parse_timestamp(0) == 0.0


def test_parse_timestamp_milliseconds_detected() -> None:
    assert parse_timestamp(1_700_000_000_123) == pytest.approx(EPOCH_SECONDS + 0.123)
    assert parse_timestamp("1700000000123") == pytest.approx(EPOCH_SECONDS + 0.123)


def test_parse_timestamp_iso8601() -> None:
    assert parse_timestamp("2023-11-14T22:13:20Z") == pytest.approx(EPOCH_SECONDS)
    assert parse_timestamp("2023-11-14T22:13:20+00:00") == pytest.approx(EPOCH_SECONDS)
    # Naive ISO strings are treated as UTC.
    assert parse_timestamp("2023-11-14T22:13:20") == pytest.approx(EPOCH_SECONDS)


@pytest.mark.parametrize("value", [None, True, False, "", "   ", "not-a-date", {}, []])
def test_parse_timestamp_rejects_non_timestamps(value: object) -> None:
    assert parse_timestamp(value) is None


def test_format_timestamp_roundtrip() -> None:
    assert format_timestamp(EPOCH_SECONDS) == "2023-11-14 22:13:20Z"


# -------------------------------------------------------------- entity fields
def test_entity_detection_follows_ranking() -> None:
    # "entity" outranks the weaker "name" key.
    assert detect_entity({"name": "Alice", "entity": "Org"}) == "Org"
    # "entity_type" outranks "entity".
    assert detect_entity({"entity": "Org", "entity_type": "corporate"}) == "corporate"
    # Falls through to the last ranked key.
    assert detect_entity({"id": "7"}) == "7"


def test_entity_detection_skips_blank_and_missing() -> None:
    assert detect_entity({}) == "(unknown)"
    assert detect_entity({"entity": "   "}) == "(unknown)"
    assert detect_entity({"entity": None, "name": "Alice"}) == "Alice"


def test_entity_ranking_is_ordered_and_unique() -> None:
    assert ENTITY_FIELD_RANKING[0] == "entity_type"
    assert list(ENTITY_FIELD_RANKING) == list(dict.fromkeys(ENTITY_FIELD_RANKING))


# ----------------------------------------------------------- timestamp fields
def test_timestamp_detection_prefers_highest_ranked_field() -> None:
    event = {"timestamp": "1700000000.25", "ts": "1"}
    assert detect_timestamp(event) == pytest.approx(1_700_000_000.25)


def test_timestamp_detection_falls_back_to_any_value() -> None:
    assert detect_timestamp({"occurred": 1_700_000_000}) == pytest.approx(EPOCH_SECONDS)
    assert detect_timestamp({"note": "no timestamp here"}) is None


# ---------------------------------------------------------------- labelling
def test_label_detection_uses_summary_before_entity() -> None:
    label = detect_label({"summary": "Filed complaint", "entity": "Org"}, "Org")
    assert label == "Filed complaint"
    assert detect_label({"entity": "Org"}, "Org") == "Org"


# ---------------------------------------------------------------- normalise
def test_normalize_events_shapes_records() -> None:
    events = normalize_events(
        [
            {"entity": "Org", "summary": "Filed", "timestamp": EPOCH_SECONDS},
            "not-a-mapping",
        ]
    )
    first, second = events
    assert first.index == 0
    assert first.entity == "Org"
    assert first.label == "Filed"
    assert first.timestamp == pytest.approx(EPOCH_SECONDS)
    assert second.index == 1
    assert second.entity == "(unknown)"
    assert second.timestamp is None
    assert second.raw == {"value": "not-a-mapping"}


def test_normalize_events_returns_frozen_records() -> None:
    (event,) = normalize_events([{"entity": "Org"}])
    with pytest.raises(FrozenInstanceError):
        event.entity = "other"  # type: ignore[misc]


def test_timeline_kernel_has_no_ui_or_site_imports() -> None:
    """The shared kernel contract is enforced by the normal test suite."""
    source = (Path(__file__).resolve().parents[1] / "timeline_kernel.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    forbidden = {"tkinter", "ui_text", "anchorum_desktop", "site"}
    imports = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    imports.update(
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert imports.isdisjoint(forbidden)
