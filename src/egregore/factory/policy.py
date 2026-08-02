"""Factory policy loader — the governance contract, fail-closed.

This file is GOVERNANCE, not configuration. Rules:

1. **Malformed policy = BLOCKED.** If the policy file cannot be parsed or is
   missing its ``qc`` block, ``load_policy`` raises ``PolicyError`` and the
   router ships nothing. A system that falls back to silent defaults when its
   governance is corrupt has no governance.
2. **Precedence: env var > policy file > code defaults.** Env wins for
   emergencies; every env override is recorded and emitted to telemetry
   (``factory.policy.override``), same honesty rule as ``QC_BYPASSED``.
3. **``policy_hash``** (SHA-256 of the canonical merged policy) goes into
   every ``factory.run.outcome`` so the histogram can be sliced by regime.
4. **Hot reload by mtime** — one ``stat`` per load call, no watcher daemon.
"""

from __future__ import annotations

import copy
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from egregore.shared.canonical import canonical_dumps, canonical_loads


class PolicyError(Exception):
    """Raised when the factory policy is missing, malformed, or invalid."""


_DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "factory_policy.json"

_CODE_DEFAULTS: dict[str, Any] = {
    "version": 0,
    "qc": {
        "fail_closed": True,
        "confidence_threshold": 0.6,
        "rework_budget": 2,
        "escalate_to": "heavy",
        "deterministic_first": True,
        "critic_timeout_ms": 60000,
        "critic_max_tokens": 256,
        "critic_model": "qwen_1.5b",
        "critic_temperature": 0.0,
        "max_output_chars": 50000,
        "forbidden_patterns": [],
        "required_output_fields": [],
    },
    "escalation": {
        "path": ["micro", "standard", "heavy"],
        "never_skip_station": True,
        "max_heavy_escalations_per_run": 1,
    },
}

# env var -> (section, key, converter). Emergency overrides; each one is
# recorded in LoadedPolicy.overrides and emitted to telemetry by the router.
_ENV_OVERRIDES: dict[str, tuple[str, str, Any]] = {
    "EGREGORE_FACTORY_QC_CONFIDENCE_THRESHOLD": ("qc", "confidence_threshold", float),
    "EGREGORE_FACTORY_QC_REWORK_BUDGET": ("qc", "rework_budget", int),
    "EGREGORE_FACTORY_QC_CRITIC_TIMEOUT_MS": ("qc", "critic_timeout_ms", int),
    "EGREGORE_FACTORY_QC_CRITIC_MAX_TOKENS": ("qc", "critic_max_tokens", int),
    "EGREGORE_FACTORY_QC_CRITIC_MODEL": ("qc", "critic_model", str),
}


@dataclass(frozen=True)
class LoadedPolicy:
    data: dict[str, Any]
    policy_hash: str
    path: Path | None
    overrides: dict[str, Any] = field(default_factory=dict)


# Cache of the parsed FILE only (path, mtime_ns, parsed). Env overrides are
# applied fresh on every load so they take effect without touching the file.
_file_cache: tuple[Path, int, dict[str, Any]] | None = None


def reset_policy_cache() -> None:
    """Drop the file cache (tests only)."""
    global _file_cache
    _file_cache = None


def _policy_path() -> Path:
    return Path(os.environ.get("EGREGORE_FACTORY_POLICY", str(_DEFAULT_PATH)))


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge one level deep (sections), then keys within sections."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key].update(value)
        else:
            out[key] = value
    return out


def _load_file(path: Path) -> dict[str, Any]:
    """Parse + validate the policy file, with mtime-based hot reload."""
    global _file_cache
    stat = path.stat()  # FileNotFoundError propagates -> caller decides
    if _file_cache is not None and _file_cache[0] == path and _file_cache[1] == stat.st_mtime_ns:
        return _file_cache[2]
    try:
        raw = canonical_loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PolicyError(f"factory policy is not valid JSON: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyError(f"factory policy must be a JSON object: {path}")
    qc = raw.get("qc")
    if not isinstance(qc, dict):
        raise PolicyError(f"factory policy missing required 'qc' object: {path}")
    _file_cache = (path, stat.st_mtime_ns, raw)
    return raw


def load_policy() -> LoadedPolicy:
    """Load the effective policy: code defaults < file < env overrides.

    Raises PolicyError (fail-closed) when the file exists but is malformed or
    an env override value cannot be converted.
    """
    path = _policy_path()
    file_data: dict[str, Any] = {}
    file_path: Path | None = None
    if path.exists():
        file_data = _load_file(path)
        file_path = path

    data = _merge(_CODE_DEFAULTS, file_data) if file_data else copy.deepcopy(_CODE_DEFAULTS)

    overrides: dict[str, Any] = {}
    for env_var, (section, key, converter) in _ENV_OVERRIDES.items():
        raw_value = os.environ.get(env_var)
        if raw_value is None:
            continue
        try:
            value = converter(raw_value)
        except (TypeError, ValueError) as exc:
            raise PolicyError(
                f"env override {env_var}={raw_value!r} is not a valid {key}: {exc}"
            ) from exc
        data.setdefault(section, {})[key] = value
        overrides[f"{section}.{key}"] = value

    policy_hash = hashlib.sha256(
        canonical_dumps(data, default=str).encode("utf-8")
    ).hexdigest()
    return LoadedPolicy(data=data, policy_hash=policy_hash, path=file_path, overrides=overrides)
