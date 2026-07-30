#!/usr/bin/env python3
"""Fix M5-DET wall-clock usage in the four cell-aware modules."""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "egregore"


def replace_in_file(path: Path, replacements: list[tuple[str, str]]) -> int:
    text = path.read_text(encoding="utf-8")
    original = text
    for old, new in replacements:
        text = text.replace(old, new)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return 1
    return 0


def main() -> int:
    changes = 0

    # application
    changes += replace_in_file(
        SRC / "application" / "integrity_watcher.py",
        [
            ("datetime.now(UTC)", "datetime.fromtimestamp(time.time_ns() / 1e9, tz=UTC)"),
        ],
    )
    changes += replace_in_file(
        SRC / "application" / "federation_treaty.py",
        [
            ("datetime.now(UTC).isoformat()", "datetime.fromtimestamp(time.time_ns() / 1e9, tz=UTC).isoformat()"),
        ],
    )
    changes += replace_in_file(
        SRC / "application" / "self_rep_dossier_builder.py",
        [
            ("datetime.now(UTC)", "datetime.fromtimestamp(time.time_ns() / 1e9, tz=UTC)"),
        ],
    )

    # http_api
    changes += replace_in_file(
        SRC / "http_api" / "http" / "v1" / "auth.py",
        [
            (
                "int(datetime.datetime.now(datetime.UTC).timestamp() * 1e9)",
                "time.time_ns()",
            ),
        ],
    )

    # infrastructure
    changes += replace_in_file(
        SRC / "infrastructure" / "deepseek_client.py",
        [
            ("int(time.time() * 1e9)", "time.time_ns()"),
        ],
    )
    changes += replace_in_file(
        SRC / "infrastructure" / "gguf_catalog.py",
        [
            ("datetime.now(UTC).isoformat()", "datetime.fromtimestamp(time.time_ns() / 1e9, tz=UTC).isoformat()"),
        ],
    )
    changes += replace_in_file(
        SRC / "infrastructure" / "local_model_client.py",
        [
            ("created_at_ns=int(time.time() * 1e9),", "created_at_ns=time.time_ns(),"),
        ],
    )
    changes += replace_in_file(
        SRC / "infrastructure" / "anthropic_client.py",
        [
            ("created_at_ns = int(time.time() * 1e9)", "created_at_ns = time.time_ns()"),
        ],
    )
    changes += replace_in_file(
        SRC / "infrastructure" / "sediment_archive.py",
        [
            ("now = datetime.now(UTC)", "now = datetime.fromtimestamp(time.time_ns() / 1e9, tz=UTC)"),
            ("fossilization_timestamp_ns=int(time.time() * 1e9),", "fossilization_timestamp_ns=time.time_ns(),"),
        ],
    )
    changes += replace_in_file(
        SRC / "infrastructure" / "persistence" / "user_repository.py",
        [
            (
                "return int(datetime.datetime.now(datetime.UTC).timestamp() * 1e9)",
                "return time.time_ns()",
            ),
        ],
    )

    # interface
    changes += replace_in_file(
        SRC / "interface" / "ombudsman_router.py",
        [
            ("(canonical_dumps(state.stage_states), time.time(), cell_id)", "(canonical_dumps(state.stage_states), time.time_ns() / 1e9, cell_id)"),
            ('"timestamp": datetime.now(UTC).isoformat(timespec="seconds")', '"timestamp": datetime.fromtimestamp(time.time_ns() / 1e9, tz=UTC).isoformat(timespec="seconds")'),
        ],
    )
    changes += replace_in_file(
        SRC / "interface" / "anchorum_router.py",
        [
            ("return datetime.now(UTC).isoformat()", "return datetime.fromtimestamp(time.time_ns() / 1e9, tz=UTC).isoformat()"),
        ],
    )
    changes += replace_in_file(
        SRC / "interface" / "dni_2_quarantine.py",
        [
            ('"timestamp_ns": int(time.time() * 1e9),', '"timestamp_ns": time.time_ns(),'),
            ("int(time.time() * 1e9)", "time.time_ns()"),
        ],
    )
    changes += replace_in_file(
        SRC / "interface" / "dashboard" / "router.py",
        [
            ("datetime.now(UTC).timestamp()", "time.time_ns() / 1e9"),
        ],
    )
    changes += replace_in_file(
        SRC / "interface" / "dashboard" / "service.py",
        [
            ("timestamp=datetime.now(UTC),", "timestamp=datetime.fromtimestamp(time.time_ns() / 1e9, tz=UTC),"),
            ("age = (datetime.now(UTC) - last_rotated).days", "age = (datetime.fromtimestamp(time.time_ns() / 1e9, tz=UTC) - last_rotated).days"),
        ],
    )

    # bootstrap: timestamp uses time.time_ns()/1e9, latency uses time.monotonic()
    bootstrap = SRC / "interface" / "bootstrap.py"
    text = bootstrap.read_text(encoding="utf-8")
    original = text
    text = text.replace(
        'status="ready", plane="projection", timestamp=time.time(), checks={}',
        'status="ready", plane="projection", timestamp=time.time_ns() / 1e9, checks={}',
    )
    text = text.replace(
        'status="alive", plane="projection", timestamp=time.time(), checks={}',
        'status="alive", plane="projection", timestamp=time.time_ns() / 1e9, checks={}',
    )
    text = text.replace(
        "start = time.time()",
        "start = time.monotonic()",
    )
    text = text.replace(
        "latency_ms = (time.time() - start) * 1000",
        "latency_ms = (time.monotonic() - start) * 1000",
    )
    text = text.replace(
        "last_seen=time.time(),",
        "last_seen=time.time_ns() / 1e9,",
    )
    if text != original:
        bootstrap.write_text(text, encoding="utf-8")
        changes += 1

    print(f"Updated {changes} files for M5-DET.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
