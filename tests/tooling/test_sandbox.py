"""Tests for the pipeline sandbox runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def signing_key() -> str:
    # Local test key; never a production secret.
    from egregore.kernel.ed25519_signer import generate_signing_key

    return generate_signing_key()


def _write_top_module(pkg_root: Path, layer: str, source: str) -> Path:
    module_dir = pkg_root / layer
    module_dir.mkdir(parents=True)
    (module_dir / "__init__.py").write_text(source, encoding="utf-8")
    return module_dir


def _run_sandbox(
    cwd: Path, signing_key: str, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "EGREGORE_SIGNING_KEY_HEX": signing_key,
        "EGREGORE_SANDBOX_SRC_ROOT": str(cwd),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(
                Path(__file__).resolve().parents[2] / "scripts" / "pipeline_sandbox.py"
            ),
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def test_sandbox_passes_clean_module(tmp_path: Path, signing_key: str) -> None:
    pkg_root = tmp_path / "src" / "egregore"
    _write_top_module(pkg_root, "application", "x = 1")

    result = _run_sandbox(tmp_path, signing_key)

    assert result.returncode == 0, result.stderr
    aggregate_path = tmp_path / "sandbox_outputs" / "aggregate_report.json"
    assert aggregate_path.exists()
    zarc_path = tmp_path / "sandbox_outputs" / "sandbox.zarc"
    assert zarc_path.exists()
    assert "Skipping M2 graph audit" in result.stdout


def test_sandbox_fails_m1_violation(tmp_path: Path, signing_key: str) -> None:
    pkg_root = tmp_path / "src" / "egregore"
    _write_top_module(
        pkg_root,
        "domain",
        "from egregore.infrastructure import something",  # domain may not import infrastructure
    )

    result = _run_sandbox(tmp_path, signing_key)

    assert result.returncode == 1
    assert "FAIL" in result.stderr or "FAIL" in result.stdout


def test_sandbox_strict_mode_fails_missing_manifest(
    tmp_path: Path, signing_key: str
) -> None:
    pkg_root = tmp_path / "src" / "egregore"
    _write_top_module(pkg_root, "application", "x = 1")

    result = _run_sandbox(tmp_path, signing_key, {"EGREGORE_SANDBOX_STRICT": "1"})

    assert result.returncode == 1
    assert "missing egregore-module.json" in result.stderr


def test_sandbox_promotes_manifest_modules_to_standard(
    tmp_path: Path, signing_key: str
) -> None:
    pkg_root = tmp_path / "src" / "egregore"
    module_dir = _write_top_module(pkg_root, "application", "x = 1")
    manifest = {
        "module_id": "egregore.application",
        "version": "0.1.0",
        "cbi0": {
            "m1_plane": "plane1",
            "m1_layer": "application",
            "m2_dependencies": [],
        },
    }
    (module_dir / "egregore-module.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    result = _run_sandbox(tmp_path, signing_key)

    assert result.returncode == 0, result.stderr
    assert "Checking application (standard)" in result.stdout
    assert "Running M2 graph audit on 1 module(s)" in result.stdout
