"""Signature verification and metadata anomaly detection for the RFE."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from egregore.kernel.ed25519_signer import verify_message
from egregore.rfe.config import RFEConfig
from egregore.rfe.models import AuthorityAssessment, Manifest, Stream
from egregore.shared.canonical import canonical_dumps


class FutureTimestampError(ValueError):
    """Raised when a stream timestamp is too far in the future."""

    def __init__(self, stream_id: str, timestamp: str, skew_seconds: float) -> None:
        super().__init__(
            f"stream {stream_id!r} timestamp {timestamp!r} is {skew_seconds:.1f}s in the future"
        )
        self.stream_id = stream_id
        self.timestamp = timestamp
        self.skew_seconds = skew_seconds


class SecurityAnalyzer:
    """Analyzes streams for signature validity and metadata anomalies."""

    def __init__(
        self, config: RFEConfig, verify_keys: dict[str, str] | None = None
    ) -> None:
        self._config = config
        self._verify_keys = verify_keys or {}

    def _parse_ts(self, ts: str) -> datetime:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt

    def _canonical_message(self, stream: Stream) -> bytes:
        """Canonical serialization of a stream excluding its signature field.

        ``exclude_none=True`` keeps the canonical message aligned with typical
        hand-authored manifests that omit optional null fields.
        """
        raw = stream.model_dump(exclude={"signature"}, mode="json", exclude_none=True)
        return canonical_dumps(raw).encode("utf-8")

    def _verify_signature(self, stream: Stream) -> tuple[bool | None, str]:
        """Return (valid?, status_message)."""
        if not stream.signature:
            return None, "unsigned"

        verify_key = self._verify_keys.get(stream.stream_id) or self._verify_keys.get(
            "default"
        )
        if not verify_key:
            return False, "no_verify_key"

        message = self._canonical_message(stream)
        valid = verify_message(
            verify_key_hex=verify_key,
            message=message,
            signature_hex=stream.signature,
        )
        return valid, "signature_valid" if valid else "signature_invalid"

    def assess_stream(
        self, stream: Stream, manifest_timestamp: str
    ) -> AuthorityAssessment:
        """Produce an authority assessment for a single stream."""
        valid, status = self._verify_signature(stream)
        anomalies: list[str] = []

        # Future timestamp check is fail-closed: raise immediately if exceeded.
        stream_dt = self._parse_ts(stream.timestamp)
        manifest_dt = self._parse_ts(manifest_timestamp)
        max_skew = float(
            self._config.red_team_config.get("future_timestamp_max_skew_seconds", 60)
        )
        skew = (stream_dt - manifest_dt).total_seconds()
        if skew > max_skew:
            raise FutureTimestampError(stream.stream_id, stream.timestamp, skew)

        if skew < -86400 * 365:
            anomalies.append("ancient_timestamp")

        effective_tier = stream.source_tier
        weight = self._config.authority_weight_for_tier(effective_tier)
        if valid is not True:
            weight *= self._config.unsigned_authority_multiplier
            anomalies.append("unsigned_or_bad_signature")

        return AuthorityAssessment(
            stream_id=stream.stream_id,
            declared_tier=stream.source_tier,
            effective_tier=effective_tier,
            authority_weight=weight,
            signature_valid=valid,
            signature_status=status,
            anomalies=anomalies,
        )

    def assess_streams(
        self,
        manifest: Manifest,
    ) -> tuple[list[AuthorityAssessment], list[str]]:
        """Assess all streams and detect cross-stream anomalies.

        Returns (per-stream assessments, global anomalies).
        """
        assessments: list[AuthorityAssessment] = []
        global_anomalies: list[str] = []

        for stream in manifest.streams:
            assessments.append(self.assess_stream(stream, manifest.timestamp))

        # Duplicate provenance_hash detection within the duplication window.
        duplication_window = float(
            self._config.red_team_config.get("duplication_window_seconds", 3600)
        )
        hashes: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for stream in manifest.streams:
            dt = self._parse_ts(stream.timestamp)
            epoch = dt.timestamp()
            hashes[stream.provenance_hash].append((stream.stream_id, epoch))

        for ph, entries in hashes.items():
            if len(entries) < 2:
                continue
            entries_sorted = sorted(entries, key=lambda x: x[1])
            for i in range(1, len(entries_sorted)):
                if (
                    entries_sorted[i][1] - entries_sorted[i - 1][1]
                    <= duplication_window
                ):
                    global_anomalies.append(f"duplicate_provenance_hash:{ph}")
                    break

        # Source flooding detection.
        flooding_threshold = int(
            self._config.red_team_config.get("flooding_rate_threshold", 10)
        )
        source_counts: dict[str, int] = defaultdict(int)
        for stream in manifest.streams:
            source_key = f"{stream.type}"
            source_counts[source_key] += 1
        for source_key, count in source_counts.items():
            if count > flooding_threshold:
                global_anomalies.append(f"source_flooding:{source_key}:{count}")

        return assessments, global_anomalies


def default_security_analyzer(
    config: RFEConfig,
    verify_keys: dict[str, str] | None = None,
) -> SecurityAnalyzer:
    """Factory for the default security analyzer."""
    return SecurityAnalyzer(config=config, verify_keys=verify_keys)
