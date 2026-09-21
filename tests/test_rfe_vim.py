"""Version Integration Module (VIM) tests for the RFE."""

from __future__ import annotations

from typing import Any

import pytest

from egregore.kernel.ed25519_signer import get_verify_key_hex
from egregore.rfe.config import load_rfe_config
from egregore.rfe.engine import reproducible_fusion
from egregore.rfe.models import Manifest
from egregore.rfe.vim import VersionIntegrationModule
from tests.redteam.conftest import sign_stream

pytestmark = [pytest.mark.redteam]


def _manifest_v1(signing_key: str) -> dict[str, Any]:
    m = {
        "case_id": "case_vim_001",
        "timestamp": "2026-06-29T00:00:00+00:00",
        "streams": [
            {
                "stream_id": "s1",
                "type": "testimony",
                "source_tier": 1,
                "content": {
                    "claim": "positive",
                    "subject": "liability",
                    "text": "Direct evidence.",
                },
                "confidence": 0.95,
                "provenance_hash": "h1",
                "signature": None,
                "timestamp": "2026-06-28T12:00:00+00:00",
                "decay": {"method": "unbounded"},
                "severity_impact": 0.9,
                "relevance_tags": ["liability"],
            }
        ],
        "constraints": {"output_format": "pdf-a-1b", "language": "en"},
    }
    m["streams"] = [sign_stream(s, signing_key) for s in m["streams"]]
    return m


def _manifest_v2(signing_key: str) -> dict[str, Any]:
    m = _manifest_v1(signing_key)
    m["streams"].append(
        sign_stream(
            {
                "stream_id": "s2",
                "type": "analyst_report",
                "source_tier": 3,
                "content": {
                    "claim": "negative",
                    "subject": "liability",
                    "text": "Contradictory analyst view.",
                },
                "confidence": 0.7,
                "provenance_hash": "h2",
                "signature": None,
                "timestamp": "2026-06-28T13:00:00+00:00",
                "decay": {"method": "unbounded"},
                "severity_impact": 0.5,
                "relevance_tags": ["liability"],
            },
            signing_key,
        )
    )
    return m


def test_vim_diff_and_synthesis(signing_key: str) -> None:
    config = load_rfe_config()
    config.setdefault("security", {})["verify_keys"] = {
        "default": get_verify_key_hex(signing_key)
    }
    first = reproducible_fusion(_manifest_v1(signing_key), config)
    second = reproducible_fusion(_manifest_v2(signing_key), config)

    vim = VersionIntegrationModule()
    manifest = Manifest.model_validate(_manifest_v2(signing_key))
    integration = vim.integrate(
        manifest=manifest,
        current_decision_log=second["report"]["decision_log"],
        previous_decision_log=first["report"]["decision_log"],
    )

    diff = integration["diff_analysis"]
    assert "s2" in diff["added_stream_ids"]
    assert "s1" in diff["retained_stream_ids"]
    assert (
        diff["version_lineage"]["previous_engine_version"]
        == first["report"]["engine_version"]
    )

    synthesis = integration["current_best_synthesis"]
    assert synthesis["synthesis_version"] == "vim-1.0.0"
    assert synthesis["current_best_conclusions"]
    assert (
        "liability: supported" in diff["previous_conclusions"]
        or "liability: opposed" in diff["previous_conclusions"]
    )
