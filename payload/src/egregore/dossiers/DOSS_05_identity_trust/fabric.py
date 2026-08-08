# epistemic marker: provenance / auditability
"""DOSS-05: Identity & Trust Fabric — Constitution, permissions, and identity management."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    obligations: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Constitution:
    version: str
    ratified_by: tuple[str, ...]
    articles: tuple[Article, ...]
    required_treaty_clauses: tuple[str, ...]
    entropy_config: dict[str, Any] = field(default_factory=dict)
    escalation_config: dict[str, Any] = field(default_factory=dict)
    meta_governor_quorum: dict[str, Any] = field(default_factory=dict)

    def article(self, article_id: str) -> Article | None:
        for article in self.articles:
            if article.id == article_id:
                return article
        return None

    def required_clauses(self) -> set[str]:
        return set(self.required_treaty_clauses)


class PermissionRegistry:
    """Role-based permission registry for the Egregore trust fabric."""

    def __init__(self) -> None:
        self._grants: dict[str, set[str]] = {}

    def grant(self, user_id: str, action: str) -> None:
        self._grants.setdefault(user_id, set()).add(action)

    def revoke(self, user_id: str, action: str) -> None:
        self._grants.get(user_id, set()).discard(action)

    def has(self, user_id: str, action: str) -> bool:
        return action in self._grants.get(user_id, set())

    def list_actions(self, user_id: str) -> list[str]:
        return sorted(self._grants.get(user_id, set()))
