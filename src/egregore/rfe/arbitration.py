"""Conflict resolution with arbitration for the Reproducible Fusion Engine."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from egregore.rfe.models import ConflictResolution, ScoredStream


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class ArbitrationEngine:
    """Resolve contradictory streams using a tier-freshness-corroboration-score rule."""

    def __init__(self, arbitration_threshold: float, dead_band: float) -> None:
        self._arbitration_threshold = arbitration_threshold
        self._dead_band = dead_band

    def _find_conflicts(self, streams: list[ScoredStream]) -> list[list[ScoredStream]]:
        """Group streams whose conclusions contradict one another.

        Conflict detection convention:
        - The engine tags each scored stream with ``claim:positive``,
          ``claim:negative``, or ``claim:neutral`` based on the raw content's
          ``claim`` field (or inferred neutrality).
        - Streams of the same ``stream_type`` with opposing claim tags are
          considered contradictory.
        - Only the strongest positive and strongest negative stream in each
          type bucket are paired for arbitration.
        """
        by_type: dict[str, dict[str, list[ScoredStream]]] = {}
        for s in streams:
            bucket = by_type.setdefault(
                s.stream_type, {"positive": [], "negative": [], "neutral": []}
            )
            if "claim:positive" in s.flags:
                bucket["positive"].append(s)
            elif "claim:negative" in s.flags:
                bucket["negative"].append(s)
            else:
                bucket["neutral"].append(s)

        conflicts: list[list[ScoredStream]] = []
        for _bucket_type, bucket in by_type.items():
            if bucket["positive"] and bucket["negative"]:
                top_positive = max(bucket["positive"], key=lambda x: x.composite_score)
                top_negative = max(bucket["negative"], key=lambda x: x.composite_score)
                conflicts.append([top_positive, top_negative])
        return conflicts

    def resolve_conflict(
        self,
        conflict_streams: list[ScoredStream],
        original_stream_data: dict[str, dict[str, Any]],
    ) -> ConflictResolution:
        """Run the arbitration procedure on a group of conflicting streams.

        Resolution rule (documented in code):
        1. Compare source authority tiers; a tier difference >= 2 wins immediately.
        2. Else compare composite scores using arbitration_threshold (>=0.15).
        3. If score gap < dead_band (0.05), force dispute.
        4. If scores are within [dead_band, arbitration_threshold), compare:
           a. freshness (more recent wins)
           b. corroboration count (more corroborating streams win)
        5. If still unresolved, declare dispute.
        """
        if len(conflict_streams) < 2:
            raise ValueError("arbitration requires at least two conflicting streams")
        a, b = conflict_streams[0], conflict_streams[1]

        conflict_id = hashlib.sha256(
            f"{a.stream_id}|{b.stream_id}".encode()
        ).hexdigest()[:16]

        tier_gap = abs(a.source_tier - b.source_tier)
        score_gap = abs(a.composite_score - b.composite_score)
        winner: ScoredStream | None = None
        loser: ScoredStream | None = None
        resolved = False
        dispute_forced = False
        rationale_parts: list[str] = []
        rule = "unresolved"

        # 1. Authority tier difference >= 2.
        if tier_gap >= 2:
            winner = a if a.source_tier < b.source_tier else b
            loser = b if winner is a else a
            resolved = True
            rule = "authority_tier_gap"
            rationale_parts.append(
                f"Tier gap {tier_gap} >= 2 ({winner.stream_id} tier {winner.source_tier} vs {loser.stream_id} tier {loser.source_tier})"
            )
        # 2. Composite score gap >= arbitration_threshold.
        elif score_gap >= self._arbitration_threshold:
            winner = a if a.composite_score > b.composite_score else b
            loser = b if winner is a else a
            resolved = True
            rule = "composite_score"
            rationale_parts.append(
                f"Score gap {score_gap:.4f} >= threshold {self._arbitration_threshold}"
            )
        # 3. Dead band forces dispute.
        elif score_gap < self._dead_band:
            dispute_forced = True
            rule = "dead_band_forced_dispute"
            rationale_parts.append(
                f"Score gap {score_gap:.4f} < dead band {self._dead_band}"
            )
        # 4. Tie-breakers: freshness, then corroboration.
        else:
            raw_a = original_stream_data.get(a.stream_id, {})
            raw_b = original_stream_data.get(b.stream_id, {})
            ts_a = _parse_ts(raw_a.get("timestamp", a.stream_id))
            ts_b = _parse_ts(raw_b.get("timestamp", b.stream_id))
            if ts_a != ts_b:
                winner = a if ts_a > ts_b else b
                loser = b if winner is a else a
                resolved = True
                rule = "freshness_tiebreak"
                rationale_parts.append(
                    f"Score gap {score_gap:.4f} within arbitration band; {winner.stream_id} is fresher"
                )
            elif a.corroboration_count != b.corroboration_count:
                winner = a if a.corroboration_count > b.corroboration_count else b
                loser = b if winner is a else a
                resolved = True
                rule = "corroboration_tiebreak"
                rationale_parts.append(
                    f"Same freshness; {winner.stream_id} has more corroboration"
                )
            else:
                dispute_forced = True
                rule = "unresolvable_tie"
                rationale_parts.append(
                    "Identical score, freshness, and corroboration within dead band"
                )

        return ConflictResolution(
            conflict_id=conflict_id,
            stream_ids=[s.stream_id for s in conflict_streams],
            resolution_rule=rule,
            resolved=resolved,
            winning_stream_id=winner.stream_id if winner else None,
            loser_stream_ids=[loser.stream_id] if loser else [],
            dispute_forced=dispute_forced,
            score_gap=score_gap,
            rationale="; ".join(rationale_parts),
        )

    def arbitrate(
        self,
        scored_streams: list[ScoredStream],
        original_stream_data: dict[str, dict[str, Any]],
    ) -> list[ConflictResolution]:
        """Run arbitration over all detected conflicts."""
        conflicts = self._find_conflicts(scored_streams)
        return [
            self.resolve_conflict(group, original_stream_data) for group in conflicts
        ]
