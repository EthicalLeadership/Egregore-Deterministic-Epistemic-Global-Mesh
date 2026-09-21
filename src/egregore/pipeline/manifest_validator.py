"""Manifest validator for egregore-module.json.

Enforces required fields, types, enum values, dependency hashes, and the
shape of capabilities/ports blocks.  Returns a list of structured errors
so the orchestrator can reject malformed manifests before governance checks.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_manifest(  # noqa: C901
    manifest: dict[str, Any],
) -> list[dict[str, str]]:
    """Validate a egregore-module.json document.

    Args:
        manifest: The manifest parsed from JSON.

    Returns:
        A list of error dicts, each with keys 'field' and 'message'.
        An empty list means the manifest is valid.

    """
    errors: list[dict[str, str]] = []

    # ---- required top-level fields ----
    for field in ("name", "version", "plane", "layer", "author", "signature"):
        if field not in manifest:
            errors.append(_err(field, "Required field missing"))
    if not isinstance(manifest.get("name"), str):
        errors.append(_err("name", "Must be a string"))
    if not isinstance(manifest.get("version"), str):
        errors.append(_err("version", "Must be a string (semver)"))
    if not isinstance(manifest.get("plane"), str):
        errors.append(_err("plane", "Must be 'plane1' or 'plane2'"))
    elif manifest["plane"] not in ("plane1", "plane2"):
        errors.append(_err("plane", f"Invalid value '{manifest['plane']}'"))
    if not isinstance(manifest.get("layer"), str):
        errors.append(_err("layer", "Must be a valid layer name"))
    elif manifest["layer"] not in (
        "application",
        "domain",
        "governance",
        "interface",
        "kernel",
        "powertrain",
        "bus",
        "infrastructure",
        "shared",
    ):
        errors.append(_err("layer", f"Unknown layer '{manifest['layer']}'"))
    if not isinstance(manifest.get("author"), str):
        errors.append(_err("author", "Must be a string"))
    if not isinstance(manifest.get("signature"), str) or not manifest.get(
        "signature", ""
    ).startswith("ed25519:"):
        errors.append(_err("signature", "Must be an Ed25519 signature string"))

    # ---- source block ----
    source = manifest.get("source")
    if not isinstance(source, dict):
        errors.append(_err("source", "Must be an object"))
    else:
        if not isinstance(source.get("repository"), str):
            errors.append(_err("source.repository", "Must be a string (git URL)"))
        if not isinstance(source.get("commit"), str):
            errors.append(_err("source.commit", "Must be a string (git hash)"))

    # ---- build block ----
    build = manifest.get("build")
    if not isinstance(build, dict):
        errors.append(_err("build", "Must be an object"))
    else:
        if not isinstance(build.get("system"), str):
            errors.append(
                _err(
                    "build.system", "Must be a string (e.g., 'nix', 'bazel', 'custom')"
                )
            )
        deps = build.get("dependencies")
        if deps is not None:
            if not isinstance(deps, list):
                errors.append(_err("build.dependencies", "Must be a list"))
            else:
                for i, dep in enumerate(deps):
                    if not isinstance(dep, dict):
                        errors.append(
                            _err(f"build.dependencies[{i}]", "Must be an object")
                        )
                        continue
                    if not isinstance(dep.get("name"), str):
                        errors.append(
                            _err(f"build.dependencies[{i}].name", "Required string")
                        )
                    if not isinstance(dep.get("version"), str):
                        errors.append(
                            _err(f"build.dependencies[{i}].version", "Required string")
                        )
                    if not isinstance(dep.get("hash"), str) or not dep.get(
                        "hash", ""
                    ).startswith("sha256:"):
                        errors.append(
                            _err(
                                f"build.dependencies[{i}].hash",
                                "Must be a sha256 hash string",
                            )
                        )

    # ---- capabilities block ----
    caps = manifest.get("capabilities")
    if caps is not None:
        if not isinstance(caps, dict):
            errors.append(_err("capabilities", "Must be an object"))
        else:
            for category in ("read", "write", "execute", "network"):
                if category in caps:
                    if not isinstance(caps[category], list):
                        errors.append(
                            _err(
                                f"capabilities.{category}", "Must be a list of patterns"
                            )
                        )
                    else:
                        for j, pattern in enumerate(caps[category]):
                            if not isinstance(pattern, str):
                                errors.append(
                                    _err(
                                        f"capabilities.{category}[{j}]",
                                        "Pattern must be a string",
                                    )
                                )

    # ---- ports block ----
    ports = manifest.get("ports")
    if ports is not None:
        if not isinstance(ports, dict):
            errors.append(_err("ports", "Must be an object"))
        else:
            for port_list_key in ("implements", "requires"):
                if port_list_key in ports:
                    if not isinstance(ports[port_list_key], list):
                        errors.append(
                            _err(
                                f"ports.{port_list_key}", "Must be a list of port names"
                            )
                        )
                    else:
                        for k, port_name in enumerate(ports[port_list_key]):
                            if not isinstance(port_name, str):
                                errors.append(
                                    _err(
                                        f"ports.{port_list_key}[{k}]",
                                        "Port name must be a string",
                                    )
                                )

    # ---- optional cell field ----
    cell = manifest.get("cell")
    if cell is not None and not isinstance(cell, str):
        errors.append(_err("cell", "Must be a string"))

    # ---- test block (optional but shape-checked) ----
    tests = manifest.get("tests")
    if tests is not None:
        if not isinstance(tests, dict):
            errors.append(_err("tests", "Must be an object"))
        else:
            if "deterministic" in tests and not isinstance(
                tests["deterministic"], bool
            ):
                errors.append(_err("tests.deterministic", "Must be boolean"))
            if "replay_correct" in tests and not isinstance(
                tests["replay_correct"], bool
            ):
                errors.append(_err("tests.replay_correct", "Must be boolean"))
            if "coverage_threshold" in tests:
                ct = tests["coverage_threshold"]
                if not isinstance(ct, (int, float)) or not (0 <= ct <= 100):
                    errors.append(
                        _err("tests.coverage_threshold", "Must be a number 0-100")
                    )

    # ---- resource block ----
    resources = manifest.get("resources")
    if resources is not None:
        if not isinstance(resources, dict):
            errors.append(_err("resources", "Must be an object"))
        else:
            for res_key in (
                "max_memory_mb",
                "max_cpu_percent",
                "max_disk_mb",
                "max_network_mbps",
            ):
                if res_key in resources and not isinstance(
                    resources[res_key], (int, float)
                ):
                    errors.append(_err(f"resources.{res_key}", "Must be a number"))

    # ---- CBI-0 block (informational) ----
    cbi0 = manifest.get("cbi0")
    if cbi0 is not None and not isinstance(cbi0, dict):
        errors.append(_err("cbi0", "Must be an object"))

    return errors


def _err(field: str, message: str) -> dict[str, str]:
    return {"field": field, "message": message}
