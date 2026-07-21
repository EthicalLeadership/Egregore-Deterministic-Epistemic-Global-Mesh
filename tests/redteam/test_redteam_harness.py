"""Red-team harness: adversarial manifests vs. the Reproducible Fusion Engine."""

from __future__ import annotations

from typing import Any

import pytest

from egregore.rfe.engine import reproducible_fusion
from egregore.rfe.security import FutureTimestampError
from tests.redteam.conftest import base_manifest, make_stream, sign_stream

pytestmark = [pytest.mark.redteam]


class RedTeamHarness:
    """Generate adversarial manifests and measure RFE resilience."""

    def __init__(
        self, signing_key: str, verify_key: str, config: dict[str, Any]
    ) -> None:
        self.signing_key = signing_key
        self.verify_key = verify_key
        self.config = config
        self.false_negatives = 0
        self.false_positives = 0
        self.total_adversarial = 0
        self.total_benign = 0

    def _run(self, manifest: dict[str, Any]) -> dict[str, Any]:
        return reproducible_fusion(manifest, self.config)

    def _signed(self, stream: dict[str, Any]) -> dict[str, Any]:
        return sign_stream(stream, self.signing_key)

    # ------------------------------------------------------------------
    # Attack generators
    # ------------------------------------------------------------------

    def decay_gaming_manifest(self) -> dict[str, Any]:
        """Strategically time short-half-life streams to suppress older evidence."""
        manifest = base_manifest("2026-06-29T00:00:00+00:00")
        # Older, high-tier evidence with long half-life.
        old = make_stream(
            "old_tier1",
            source_tier=1,
            claim="positive",
            timestamp="2026-06-01T00:00:00+00:00",
            half_life_hours=720,
        )
        # New, low-tier evidence with very short half-life designed to look fresh.
        new = make_stream(
            "new_tier5_short",
            source_tier=5,
            claim="negative",
            timestamp="2026-06-28T23:00:00+00:00",
            half_life_hours=1,
        )
        manifest["streams"] = [self._signed(old), self._signed(new)]
        return manifest

    def authority_spoofing_manifest(self) -> dict[str, Any]:
        """Tier 5 stream claims tier 1 but is unsigned / has bad signature."""
        manifest = base_manifest("2026-06-29T00:00:00+00:00")
        spoofed = make_stream(
            "spoofed_tier1",
            source_tier=1,  # Claims tier 1
            claim="positive",
            timestamp="2026-06-28T12:00:00+00:00",
            half_life_hours=720,
        )
        # Actually originated from a low-tier source, but the manifest cannot know.
        # We simulate spoofing by making the signature invalid.
        spoofed["signature"] = "bad_signature_hex_0000"
        legit = make_stream(
            "legit_tier5",
            source_tier=5,
            claim="negative",
            timestamp="2026-06-28T12:00:00+00:00",
            half_life_hours=720,
        )
        manifest["streams"] = [spoofed, self._signed(legit)]
        return manifest

    def synthetic_dispute_manifest(self) -> dict[str, Any]:
        """Two high-tier streams with exactly opposite content and high confidence."""
        manifest = base_manifest("2026-06-29T00:00:00+00:00")
        a = make_stream(
            "dispute_pro",
            source_tier=1,
            claim="positive",
            timestamp="2026-06-28T12:00:00+00:00",
            half_life_hours=720,
            confidence=0.95,
        )
        b = make_stream(
            "dispute_con",
            source_tier=1,
            claim="negative",
            timestamp="2026-06-28T12:00:00+00:00",
            half_life_hours=720,
            confidence=0.95,
        )
        manifest["streams"] = [self._signed(a), self._signed(b)]
        return manifest

    def future_timestamp_manifest(self) -> dict[str, Any]:
        """Stream timestamped far in the future relative to the manifest."""
        manifest = base_manifest("2026-06-29T00:00:00+00:00")
        future = make_stream(
            "future_stream",
            source_tier=2,
            claim="positive",
            timestamp="2026-06-29T02:00:00+00:00",  # 2 hours in the future > 60s skew
            half_life_hours=720,
        )
        manifest["streams"] = [self._signed(future)]
        return manifest

    def flooding_manifest(self) -> dict[str, Any]:
        """100 streams from a single source type within one minute."""
        manifest = base_manifest("2026-06-29T00:00:00+00:00")
        streams: list[dict[str, Any]] = []
        for i in range(100):
            total_seconds = i * 60 / 100  # 100 streams evenly across 60 seconds
            seconds = int(total_seconds)
            microseconds = int((total_seconds - seconds) * 1_000_000)
            s = make_stream(
                f"flood_{i:03d}",
                source_tier=5,
                claim="positive" if i % 2 == 0 else "negative",
                timestamp=f"2026-06-29T00:00:{seconds:02d}.{microseconds:06d}+00:00",
                half_life_hours=1,
                stype="social_media",
            )
            streams.append(self._signed(s))
        manifest["streams"] = streams
        return manifest

    def benign_manifest(self) -> dict[str, Any]:
        """A clean, well-formed manifest with no adversarial patterns."""
        manifest = base_manifest("2026-06-29T00:00:00+00:00")
        a = make_stream(
            "benign_tier2",
            source_tier=2,
            claim="positive",
            timestamp="2026-06-28T12:00:00+00:00",
            half_life_hours=720,
        )
        b = make_stream(
            "benign_tier3",
            source_tier=3,
            claim="positive",
            timestamp="2026-06-28T13:00:00+00:00",
            half_life_hours=168,
        )
        manifest["streams"] = [self._signed(a), self._signed(b)]
        return manifest

    # ------------------------------------------------------------------
    # Assertion helpers
    # ------------------------------------------------------------------

    def assert_decay_gaming_exposed(self) -> None:
        self.total_adversarial += 1
        result = self._run(self.decay_gaming_manifest())
        sections = {s["name"]: s for s in result["report"]["sections"]}
        assert (
            "sensitivity_appendix" in sections
        ), "FN: decay gaming not exposed in sensitivity appendix"
        appendix = sections["sensitivity_appendix"]["rendered"]
        assert "half_life_plus_50" in appendix and "half_life_minus_50" in appendix

    def assert_spoof_downgraded(self) -> None:
        self.total_adversarial += 1
        result = self._run(self.authority_spoofing_manifest())
        dl = result["report"]["decision_log"]
        spoof = next(
            (
                a
                for a in dl["authority_assessments"]
                if a["stream_id"] == "spoofed_tier1"
            ),
            None,
        )
        assert spoof is not None
        expected = 1.0 * 0.5  # tier 1 weight halved
        assert (
            abs(spoof["authority_weight"] - expected) < 1e-9
        ), "FN: spoofed stream not downgraded"
        assert (
            "unsigned_or_bad_signature" in spoof["anomalies"]
        ), "FN: spoof anomaly not flagged"

    def assert_synthetic_dispute_arbitrated(self) -> None:
        self.total_adversarial += 1
        result = self._run(self.synthetic_dispute_manifest())
        dl = result["report"]["decision_log"]
        assert dl["conflicts"], "FN: synthetic dispute not routed to arbitration"
        # With identical tier/score/freshness/corroboration, dispute should be forced.
        dispute = dl["conflicts"][0]
        assert (
            dispute["dispute_forced"] or not dispute["resolved"]
        ), "FN: identical dispute resolved arbitrarily"

    def assert_future_timestamp_rejected(self) -> None:
        self.total_adversarial += 1
        with pytest.raises(FutureTimestampError):
            self._run(self.future_timestamp_manifest())

    def assert_flooding_alerted(self) -> None:
        self.total_adversarial += 1
        result = self._run(self.flooding_manifest())
        dl = result["report"]["decision_log"]
        anomalies = dl["anomalies"]
        flooding_anomalies = [
            a for a in anomalies if a.startswith("source_flooding:social_media")
        ]
        assert flooding_anomalies, "FN: source flooding not detected"

    def assert_benign_passes(self) -> None:
        self.total_benign += 1
        result = self._run(self.benign_manifest())
        dl = result["report"]["decision_log"]
        # Benign manifests should not be flagged as adversarial.
        adversarial_anomalies = [
            a
            for a in dl["anomalies"]
            if "flooding" in a or "duplicate" in a or "unsigned" in a
        ]
        if adversarial_anomalies:
            self.false_positives += 1

    def summary(self) -> dict[str, Any]:
        return {
            "total_adversarial": self.total_adversarial,
            "total_benign": self.total_benign,
            "false_negatives": self.false_negatives,
            "false_positives": self.false_positives,
        }


@pytest.fixture
def harness(
    signing_key: str, verify_key: str, rfe_config: dict[str, Any]
) -> RedTeamHarness:
    return RedTeamHarness(signing_key, verify_key, rfe_config)


def test_decay_gaming_exposed(harness: RedTeamHarness) -> None:
    harness.assert_decay_gaming_exposed()


def test_authority_spoofing_downgraded(harness: RedTeamHarness) -> None:
    harness.assert_spoof_downgraded()


def test_synthetic_dispute_arbitrated(harness: RedTeamHarness) -> None:
    harness.assert_synthetic_dispute_arbitrated()


def test_future_timestamp_rejected(harness: RedTeamHarness) -> None:
    harness.assert_future_timestamp_rejected()


def test_flooding_alerted(harness: RedTeamHarness) -> None:
    harness.assert_flooding_alerted()


def test_benign_manifest_low_false_positive(harness: RedTeamHarness) -> None:
    """Run benign manifests many times and assert FP rate < 5%."""
    for _ in range(20):
        harness.assert_benign_passes()
    summary = harness.summary()
    fp_rate = summary["false_positives"] / max(summary["total_benign"], 1)
    assert fp_rate < 0.05, f"FP rate {fp_rate:.2%} exceeds 5%"
    assert (
        summary["false_negatives"] == 0
    ), "At least one adversarial pattern was not flagged"
