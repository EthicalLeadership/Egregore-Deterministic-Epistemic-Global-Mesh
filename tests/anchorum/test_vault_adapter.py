"""Tests for the ANCHORUM filesystem vault adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from anchorum.forensic.core.document.vault_adapter import FilesystemVaultAdapter


@pytest.fixture
def vault(tmp_path: Path) -> FilesystemVaultAdapter:
    return FilesystemVaultAdapter(tmp_path, node_id="test-node")


def test_store_and_retrieve(vault: FilesystemVaultAdapter, tmp_path: Path) -> None:
    source = tmp_path / "contract.pdf"
    source.write_bytes(b"fake pdf content")

    receipt = vault.store(source, artifact_id="contract-001")
    assert receipt.success
    assert receipt.artifact_id == "contract-001"
    assert receipt.stored_path.exists()
    assert receipt.manifest_path.exists()
    assert receipt.manifest_path.read_text().startswith("{")

    retrieved = vault.retrieve("contract-001")
    assert retrieved == receipt.stored_path


def test_store_missing_file(vault: FilesystemVaultAdapter, tmp_path: Path) -> None:
    missing = tmp_path / "ghost.pdf"
    receipt = vault.store(missing)
    assert not receipt.success
    assert "does not exist" in (receipt.error_message or "")


def test_retrieve_missing(vault: FilesystemVaultAdapter) -> None:
    assert vault.retrieve("no-such-id") is None
