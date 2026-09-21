from __future__ import annotations

import importlib
import os
from pathlib import Path


def _tmp_dir() -> Path:
    base = os.environ.get("EGREGORE_TMP_DIR", "tmp")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _zarc_signing_key_hex() -> str:
    key = os.environ.get("EGREGORE_ZARC_SIGNING_KEY_HEX")
    if not key:
        raise RuntimeError("EGREGORE_ZARC_SIGNING_KEY_HEX is not set")
    return key


def _zarc_path(name: str) -> Path:
    return _tmp_dir() / name


def build_http_core_and_edge_journals(
    *, prev_hash_init: str = "0" * 64
) -> tuple[object, object]:
    """
    Application-layer factory for journal-backed persistence.

    Architecture note:
    - Tests forbid *static* imports of `egregore.infrastructure` from `application/`.
    - Therefore we dynamically import `ZarcJournal` inside this function.
    """
    mod = importlib.import_module("egregore.infrastructure.zarc_journal")
    ZarcJournal = mod.ZarcJournal  # noqa: N806

    core = ZarcJournal(
        zarc_path=_zarc_path("egregore_http_core.zarc"),
        signing_key_hex=_zarc_signing_key_hex(),
        prev_hash_init=prev_hash_init,
    )
    edge = ZarcJournal(
        zarc_path=_zarc_path("egregore_http_edge.zarc"),
        signing_key_hex=_zarc_signing_key_hex(),
        prev_hash_init=prev_hash_init,
    )
    return core, edge
