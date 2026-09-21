"""Deterministic model selection over the GGUF catalog + profile manifest.

Pure and fail-closed: profiles come from ``config/model_profiles.json``
(``MODEL_PROFILES_PATH``), the catalog is the filesystem mirror from
``GGUFCatalog.get_catalog()``. No heuristics, no network, no mutation.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ALLOWED_TASKS = ("general", "code", "legal", "fast")

_REQUIRED_PROFILE_FIELDS = ("catalog_key", "routes_to", "task_tags", "quality_score")


class NoModelAvailableError(RuntimeError):
    """No catalogued model matches the request and no fallback exists."""


@dataclass(frozen=True)
class ModelProfile:
    logical_id: str
    catalog_key: str
    routes_to: str
    task_tags: tuple[str, ...]
    quality_score: int
    size_bytes: int


@dataclass(frozen=True)
class SelectionResult:
    logical_id: str
    routes_to: str
    catalog_key: str
    reason: str
    fallback_used: bool

    @property
    def model_id(self) -> str:
        """Backend-routable model identifier (GgufBackend env name)."""
        return self.routes_to


def resolve_profiles_path(path: str | None = None) -> Path:
    """Resolve the profiles manifest path (env > arg > repo default)."""
    raw = path or os.environ.get("MODEL_PROFILES_PATH", "config/model_profiles.json")
    p = Path(raw)
    if not p.is_absolute():
        from egregore.shared.paths import repo_root

        p = repo_root() / p
    return p


def load_profiles(path: str | None = None) -> dict[str, dict[str, Any]]:
    """Load the profiles manifest. Malformed JSON fails closed (ValueError)."""
    p = resolve_profiles_path(path)
    try:
        with open(p) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Model profiles manifest unreadable: {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Model profiles manifest must be a JSON object: {p}")
    for logical_id, profile in data.items():
        missing = [f for f in _REQUIRED_PROFILE_FIELDS if f not in profile]
        if missing:
            raise ValueError(
                f"Profile '{logical_id}' missing fields {missing} in {p}"
            )
    return data


class ModelSelector:
    """Pick the best available model for a task type.

    Ranking: quality_score desc, size_bytes asc, logical_id asc (fully
    deterministic). Profiles whose catalog_key has no on-disk entry are
    skipped. If no profile matches the task tag, the best overall model is
    returned with ``fallback_used=True``.
    """

    def __init__(
        self,
        catalog: dict[str, dict[str, Any]],
        profiles: dict[str, dict[str, Any]],
    ) -> None:
        self._catalog = catalog
        self._profiles = profiles

    def _candidates(self) -> list[ModelProfile]:
        candidates: list[ModelProfile] = []
        for logical_id, profile in self._profiles.items():
            cat_entry = self._catalog.get(profile["catalog_key"])
            if not cat_entry:
                continue  # file missing / not catalogued
            candidates.append(
                ModelProfile(
                    logical_id=logical_id,
                    catalog_key=profile["catalog_key"],
                    routes_to=profile["routes_to"],
                    task_tags=tuple(profile["task_tags"]),
                    quality_score=int(profile["quality_score"]),
                    size_bytes=int(cat_entry.get("size_bytes", 0)),
                )
            )
        return candidates

    @staticmethod
    def _rank(candidates: list[ModelProfile]) -> list[ModelProfile]:
        return sorted(
            candidates,
            key=lambda c: (-c.quality_score, c.size_bytes, c.logical_id),
        )

    def select(self, task_type: str = "general") -> SelectionResult:
        if task_type not in ALLOWED_TASKS:
            raise ValueError(
                f"Invalid task: {task_type}. Allowed: {list(ALLOWED_TASKS)}"
            )
        candidates = self._candidates()
        if not candidates:
            raise NoModelAvailableError(
                "No profiled models are present in the GGUF catalog"
            )

        matched = self._rank([c for c in candidates if task_type in c.task_tags])
        if matched:
            best = matched[0]
            return SelectionResult(
                logical_id=best.logical_id,
                routes_to=best.routes_to,
                catalog_key=best.catalog_key,
                reason=(
                    f"best quality_score={best.quality_score} "
                    f"for task '{task_type}'"
                ),
                fallback_used=False,
            )

        best = self._rank(candidates)[0]
        return SelectionResult(
            logical_id=best.logical_id,
            routes_to=best.routes_to,
            catalog_key=best.catalog_key,
            reason=(
                f"no profile tagged '{task_type}'; fell back to highest "
                f"quality_score={best.quality_score}"
            ),
            fallback_used=True,
        )
