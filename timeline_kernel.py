"""UI-independent timeline normalization and value helpers.

This module is the shared timeline kernel. It must remain free of Tk, desktop
widgets, and site-layer imports so it can be used by the workspace and site
adapters alike.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

ENTITY_FIELD_RANKING: tuple[str, ...] = (
    "entity_type", "entity", "entity_id", "actor", "participant",
    "counterparty", "person", "organisation", "organization", "org",
    "name", "subject", "id",
)
TIMESTAMP_FIELD_RANKING: tuple[str, ...] = (
    "timestamp", "ts", "occurred_at", "event_time", "datetime", "time",
    "date", "when",
)
LABEL_FIELD_RANKING: tuple[str, ...] = (
    "summary", "description", "title", "label", "event", "action",
    "kind", "type",
)
UNKNOWN_ENTITY = "(unknown)"
_MILLIS_THRESHOLD = 1e11
_NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def _as_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _from_epoch(number: float) -> float:
    return number / 1000.0 if abs(number) >= _MILLIS_THRESHOLD else number


def parse_timestamp(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if _NUMERIC_RE.match(text):
        try:
            return _from_epoch(float(text))
        except (TypeError, ValueError, OverflowError):
            return None
    iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.timestamp()
    except (OSError, OverflowError, ValueError):
        return None


def format_timestamp(epoch_seconds: float) -> str:
    try:
        moment = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return str(epoch_seconds)
    return moment.strftime("%Y-%m-%d %H:%M:%SZ")


def detect_entity(event: Mapping[str, Any]) -> str:
    for key in ENTITY_FIELD_RANKING:
        text = _as_text(event.get(key))
        if text is not None:
            return text
    return UNKNOWN_ENTITY


def detect_timestamp(event: Mapping[str, Any]) -> float | None:
    for key in TIMESTAMP_FIELD_RANKING:
        if key in event:
            stamp = parse_timestamp(event.get(key))
            if stamp is not None:
                return stamp
    for value in event.values():
        stamp = parse_timestamp(value)
        if stamp is not None:
            return stamp
    return None


def detect_label(event: Mapping[str, Any], entity: str) -> str:
    for key in LABEL_FIELD_RANKING:
        text = _as_text(event.get(key))
        if text is not None:
            return text
    return entity


@dataclass(frozen=True)
class TimelineEvent:
    index: int
    timestamp: float | None
    entity: str
    label: str
    raw: dict[str, Any]


def normalize_events(events: Iterable[Any]) -> list[TimelineEvent]:
    normalized: list[TimelineEvent] = []
    for index, item in enumerate(events):
        record = dict(item) if isinstance(item, Mapping) else {"value": item}
        entity = detect_entity(record)
        normalized.append(
            TimelineEvent(
                index=index,
                timestamp=detect_timestamp(record),
                entity=entity,
                label=detect_label(record, entity),
                raw=record,
            )
        )
    return normalized


__all__ = [
    "ENTITY_FIELD_RANKING", "LABEL_FIELD_RANKING", "TIMESTAMP_FIELD_RANKING",
    "TimelineEvent", "detect_entity", "detect_label", "detect_timestamp",
    "format_timestamp", "normalize_events", "parse_timestamp",
]
