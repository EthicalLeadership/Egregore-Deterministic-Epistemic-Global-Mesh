from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import yaml

from egregore.interface.domain_data_ports import ConstitutionDataSource


class TreatyState(StrEnum):
    PROPOSED = "PROPOSED"
    RATIFIED = "RATIFIED"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class EscalationLevel(StrEnum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    OVERRIDE = "OVERRIDE"


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
    entropy_config: dict[str, Any]
    escalation_config: dict[str, Any]
    meta_governor_quorum: dict[str, Any]

    def article(self, article_id: str) -> Article | None:
        for article in self.articles:
            if article.id == article_id:
                return article
        return None

    def required_clauses(self) -> set[str]:
        return set(self.required_treaty_clauses)


@dataclass(frozen=True)
class Treaty:
    treaty_id: str
    parties: tuple[str, ...]
    clauses: tuple[str, ...]
    state: TreatyState
    proposed_at: str
    ratified_at: str | None = None
    expires_at: str | None = None
    signatures: dict[str, str] = field(default_factory=dict)

    def is_active(self) -> bool:
        return self.state == TreatyState.ACTIVE

    def missing_clauses(self, constitution: Constitution) -> set[str]:
        return constitution.required_clauses() - set(self.clauses)


@dataclass(frozen=True)
class EntropySignal:
    source_node_id: str
    signal_type: str
    value: float
    confidence: float
    timestamp_ns: int
    signature: str | None = None


@dataclass(frozen=True)
class Escalation:
    escalation_id: str
    level: EscalationLevel
    trigger: str
    affected_nodes: tuple[str, ...]
    evidence_hashes: tuple[str, ...]
    timestamp_ns: int
    resolved_at_ns: int | None = None


@dataclass(frozen=True)
class QuorumVote:
    proposal_hash: str
    voter_node_id: str
    decision: bool
    timestamp_ns: int
    signature: str | None = None


def load_constitution(raw_yaml: str) -> Constitution:
    """Parse an already-loaded constitution YAML into the domain model.

    Callers in Plane 2 (application/infrastructure) are responsible for reading
    the YAML file from disk and passing its text here.
    """
    parsed = yaml.safe_load(raw_yaml)
    if not isinstance(parsed, dict):
        raise ValueError("constitution YAML must contain a mapping")

    articles = tuple(
        Article(
            id=str(a["id"]),
            title=str(a["title"]),
            obligations=tuple(str(o) for o in a.get("obligations", [])),
        )
        for a in parsed.get("articles", [])
    )
    return Constitution(
        version=str(parsed.get("version", "unknown")),
        ratified_by=tuple(str(c) for c in parsed.get("ratified_by", [])),
        articles=articles,
        required_treaty_clauses=tuple(
            str(c) for c in parsed.get("required_treaty_clauses", [])
        ),
        entropy_config=dict(parsed.get("entropy", {})),
        escalation_config=dict(parsed.get("escalation", {})),
        meta_governor_quorum=dict(parsed.get("meta_governor_quorum", {})),
    )


def load_constitution_from_source(source: ConstitutionDataSource) -> Constitution:
    """Load and parse the constitution from a ratified data source.

    This is the governable entrypoint: the domain depends on the formal
    ``ConstitutionDataSource`` port, not on any concrete storage adapter.
    """
    return load_constitution(source.load().decode("utf-8"))
