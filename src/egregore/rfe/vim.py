"""Version Integration Module (VIM) for cross-version synthesis."""

from __future__ import annotations

from typing import Any

from egregore.rfe.models import DecisionLog, Manifest, ScoredStream


class VersionIntegrationModule:
    """Diff previous decision log against current manifest and produce unified synthesis."""

    def diff_analysis(
        self,
        manifest: Manifest,
        previous_decision_log: dict[str, Any],
    ) -> dict[str, Any]:
        """Compare current manifest streams with previous decision log."""
        current_ids = {s.stream_id for s in manifest.streams}
        previous_streams = previous_decision_log.get("scored_streams", [])
        previous_ids = {s["stream_id"] for s in previous_streams if isinstance(s, dict)}

        added = sorted(current_ids - previous_ids)
        removed = sorted(previous_ids - current_ids)
        retained = sorted(current_ids & previous_ids)

        changed: list[dict[str, Any]] = []
        previous_by_id = {
            s["stream_id"]: s for s in previous_streams if isinstance(s, dict)
        }
        for stream in manifest.streams:
            prev = previous_by_id.get(stream.stream_id)
            if prev is None:
                continue
            deltas: dict[str, Any] = {}
            if prev.get("source_tier") != stream.source_tier:
                deltas["source_tier"] = {
                    "from": prev.get("source_tier"),
                    "to": stream.source_tier,
                }
            if abs(float(prev.get("confidence", 0)) - stream.confidence) > 1e-9:
                deltas["confidence"] = {
                    "from": prev.get("confidence"),
                    "to": stream.confidence,
                }
            if prev.get("decay_method") != (
                stream.decay.method if stream.decay else "unbounded"
            ):
                deltas["decay_method"] = {
                    "from": prev.get("decay_method"),
                    "to": stream.decay.method if stream.decay else "unbounded",
                }
            if deltas:
                changed.append({"stream_id": stream.stream_id, "deltas": deltas})

        previous_conclusions = set(
            previous_decision_log.get("baseline_conclusions", [])
        )
        current_conclusions = set(self._infer_conclusions(manifest))
        flipped_to_support = sorted(current_conclusions - previous_conclusions)
        flipped_to_opposition = sorted(previous_conclusions - current_conclusions)

        return {
            "added_stream_ids": added,
            "removed_stream_ids": removed,
            "retained_stream_ids": retained,
            "changed_streams": changed,
            "previous_conclusions": sorted(previous_conclusions),
            "current_conclusions": sorted(current_conclusions),
            "flipped_to_support": flipped_to_support,
            "flipped_to_opposition": flipped_to_opposition,
            "version_lineage": {
                "previous_engine_version": previous_decision_log.get("engine_version"),
                "previous_policy_version": previous_decision_log.get("policy_version"),
                "previous_reasoning_version_id": previous_decision_log.get(
                    "reasoning_version_id"
                ),
            },
        }

    def _infer_conclusions(self, manifest: Manifest) -> list[str]:
        """Infer a simple list of conclusion statements from stream claims."""
        conclusions: list[str] = []
        for stream in manifest.streams:
            claim = stream.content.get("claim")
            subject = stream.content.get("subject", stream.stream_id)
            if claim == "positive":
                conclusions.append(f"{subject}: supported")
            elif claim == "negative":
                conclusions.append(f"{subject}: opposed")
        return conclusions

    def current_best_synthesis(
        self,
        manifest: Manifest,
        current_decision_log: DecisionLog | dict[str, Any],
        previous_decision_log: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Produce the unified best synthesis across versions."""
        if isinstance(current_decision_log, DecisionLog):
            current_conclusions = list(current_decision_log.baseline_conclusions or [])
        else:
            current_conclusions = list(
                current_decision_log.get("baseline_conclusions", [])
            )
        if not current_conclusions:
            current_conclusions = self._infer_conclusions(manifest)

        previous_conclusions: list[str] = []
        if previous_decision_log:
            previous_conclusions = previous_decision_log.get("baseline_conclusions", [])

        stable_conclusions = sorted(
            set(current_conclusions) & set(previous_conclusions)
        )
        new_conclusions = sorted(set(current_conclusions) - set(previous_conclusions))
        dropped_conclusions = sorted(
            set(previous_conclusions) - set(current_conclusions)
        )

        return {
            "synthesis_version": "vim-1.0.0",
            "current_best_conclusions": current_conclusions,
            "stable_across_versions": stable_conclusions,
            "new_in_current_version": new_conclusions,
            "dropped_from_previous_version": dropped_conclusions,
            "confidence_summary": {
                "streams_used": (
                    current_decision_log.streams_accepted
                    if isinstance(current_decision_log, DecisionLog)
                    else current_decision_log.get("streams_accepted", 0)
                ),
                "highest_tier": min(
                    (
                        (
                            s.source_tier
                            if isinstance(s, ScoredStream)
                            else s["source_tier"]
                        )
                        for s in (
                            current_decision_log.scored_streams
                            if isinstance(current_decision_log, DecisionLog)
                            else current_decision_log.get("scored_streams", [])
                        )
                    ),
                    default=None,
                ),
                "lowest_tier": max(
                    (
                        (
                            s.source_tier
                            if isinstance(s, ScoredStream)
                            else s["source_tier"]
                        )
                        for s in (
                            current_decision_log.scored_streams
                            if isinstance(current_decision_log, DecisionLog)
                            else current_decision_log.get("scored_streams", [])
                        )
                    ),
                    default=None,
                ),
            },
        }

    def integrate(
        self,
        manifest: Manifest,
        current_decision_log: DecisionLog,
        previous_decision_log: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Full VIM output: diff analysis + current best synthesis."""
        diff: dict[str, Any]
        if previous_decision_log:
            diff = self.diff_analysis(manifest, previous_decision_log)
        else:
            diff = {
                "added_stream_ids": sorted({s.stream_id for s in manifest.streams}),
                "removed_stream_ids": [],
                "retained_stream_ids": [],
                "changed_streams": [],
                "previous_conclusions": [],
                "current_conclusions": self._infer_conclusions(manifest),
                "flipped_to_support": [],
                "flipped_to_opposition": [],
                "version_lineage": None,
            }
        synthesis = self.current_best_synthesis(
            manifest, current_decision_log, previous_decision_log
        )
        return {"diff_analysis": diff, "current_best_synthesis": synthesis}
