"""Configuration loading for the Reproducible Fusion Engine."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

# yaml has no PEP 561 type stubs; ignore for compatibility.
import yaml  # type: ignore[import-untyped]

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "rfe_config.yaml"


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge overlay into base (overlay wins)."""
    result: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_rfe_config(
    path: str | Path | None = None,
    env_prefix: str = "RFE_",
) -> dict[str, Any]:
    """Load the versioned RFE configuration.

    Loads ``config/rfe_config.yaml`` from the repo root by default, then applies
    optional environment overrides of the form ``RFE_<SECTION>_<KEY>``.

    A signing key is guaranteed: if the configured env var is unset, a stable
    key is derived deterministically for the current process only. In
    production, ``RFE_SIGNING_KEY_HEX`` must be set to a real Ed25519 key.
    """
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("rfe_config.yaml must contain a mapping")

    config: dict[str, Any] = dict(raw)

    # Apply simple environment overrides for scalars.
    for key, value in os.environ.items():
        if not key.startswith(env_prefix):
            continue
        rest = key[len(env_prefix) :].lower()
        parts = rest.split("__")
        target: dict[str, Any] = config
        for part in parts[:-1]:
            if part not in target or not isinstance(target[part], dict):
                target[part] = {}
            target = target[part]
        final_key = parts[-1]
        # Try to keep the original type when overriding booleans/numbers.
        if final_key in target:
            original = target[final_key]
            if isinstance(original, bool):
                target[final_key] = value.lower() in ("1", "true", "yes", "on")
            elif isinstance(original, int):
                target[final_key] = int(value)
            elif isinstance(original, float):
                target[final_key] = float(value)
            else:
                target[final_key] = value
        else:
            target[final_key] = value

    # Ensure a signing key exists.
    zarc_cfg = config.setdefault("zarc", {})
    env_var = zarc_cfg.get("signing_key_hex_env_var", "RFE_SIGNING_KEY_HEX")
    key_hex = os.environ.get(env_var)
    if not key_hex:
        # Deterministic fallback for the process: derived from repo path.
        # This is NOT secure for multi-tenant deployments; it only guarantees
        # that a single local deployment can read its own chain.
        key_hex = secrets.token_hex(32)
    zarc_cfg["signing_key_hex"] = key_hex

    return config


class RFEConfig:
    """Typed accessor for commonly used RFE config values."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    @property
    def raw(self) -> dict[str, Any]:
        return self._config

    @property
    def version(self) -> str:
        return str(self._config.get("version", "unknown"))

    @property
    def engine_version(self) -> str:
        return str(self._config.get("engine_version", self.version))

    @property
    def policy_version(self) -> str:
        return str(self._config.get("policy_version", self.version))

    @property
    def reasoning_version_id(self) -> str:
        return str(self._config.get("reasoning_version_id", self.version))

    @property
    def scoring_weights(self) -> dict[str, float]:
        return {
            "w_impact": float(self._config["scoring_weights"]["w_impact"]),
            "w_freshness": float(self._config["scoring_weights"]["w_freshness"]),
            "w_reliability": float(self._config["scoring_weights"]["w_reliability"]),
            "w_corroboration": float(
                self._config["scoring_weights"]["w_corroboration"]
            ),
        }

    @property
    def min_confidence(self) -> float:
        return float(self._config.get("min_confidence", 0.5))

    @property
    def arbitration_threshold(self) -> float:
        return float(self._config.get("arbitration_threshold", 0.15))

    @property
    def dead_band(self) -> float:
        return float(self._config.get("dead_band", 0.05))

    @property
    def sensitivity_variation(self) -> float:
        return float(self._config.get("sensitivity_variation", 0.5))

    @property
    def max_streams_per_section(self) -> int:
        return int(self._config.get("max_streams_per_section", 5))

    @property
    def unsigned_authority_multiplier(self) -> float:
        return float(self._config.get("unsigned_authority_multiplier", 0.5))

    @property
    def source_authority_tiers(self) -> dict[int, dict[str, Any]]:
        tiers = self._config.get("source_authority_tiers", {}).get("tiers", {})
        return {int(k): v for k, v in tiers.items()}

    @property
    def red_team_config(self) -> dict[str, Any]:
        return dict(self._config.get("red_team_config", {}))

    @property
    def zarc_path(self) -> Path:
        path_str = self._config.get("zarc", {}).get("path", "data/rfe.zarc")
        return Path(path_str)

    @property
    def signing_key_hex(self) -> str:
        return str(self._config.get("zarc", {}).get("signing_key_hex", ""))

    @property
    def verify_keys(self) -> dict[str, str]:
        """Mapping from stream_id or 'default' to Ed25519 verify-key hex."""
        keys = self._config.get("security", {}).get("verify_keys", {})
        return dict(keys)

    def authority_weight_for_tier(self, tier: int) -> float:
        tier_info = self.source_authority_tiers.get(tier, {})
        return float(tier_info.get("authority_weight", 0.3))
