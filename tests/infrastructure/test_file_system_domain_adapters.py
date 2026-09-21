from __future__ import annotations

import contextlib
import os
import threading
import time
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from egregore.infrastructure.file_system_domain_adapters import (
    FileSystemConstitutionAdapter,
    FileSystemDossierAdapter,
    FileSystemRuleRegistryAdapter,
)

# --------------------------------------------------------------------------- #
# Positive / smoke tests
# --------------------------------------------------------------------------- #


def test_constitution_adapter_loads_bytes(tmp_path: Path) -> None:
    path = tmp_path / "constitution.yaml"
    path.write_text("version: '1.0.0'\n")
    adapter = FileSystemConstitutionAdapter(path)
    assert adapter.load() == b"version: '1.0.0'\n"


def test_constitution_adapter_missing_raises(tmp_path: Path) -> None:
    adapter = FileSystemConstitutionAdapter(tmp_path / "missing.yaml")
    with pytest.raises(FileNotFoundError):
        adapter.load()


def test_rule_registry_adapter_loads_bytes(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text("rules: []\n")
    adapter = FileSystemRuleRegistryAdapter(path)
    assert adapter.load() == b"rules: []\n"


def test_dossier_adapter_reads_relative_file(tmp_path: Path) -> None:
    (tmp_path / "evidence.txt").write_text("evidence")
    adapter = FileSystemDossierAdapter(tmp_path)
    assert adapter.read_text("evidence.txt") == "evidence"
    assert adapter.exists("evidence.txt") is True
    assert adapter.exists("missing.txt") is False


def test_dossier_adapter_list_files(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.yaml").write_text("b")
    adapter = FileSystemDossierAdapter(tmp_path)
    files = adapter.list_files(".", "*.txt")
    assert len(files) == 1
    assert files[0].endswith("a.txt")


# --------------------------------------------------------------------------- #
# Path traversal & canonicalisation
# --------------------------------------------------------------------------- #


def test_dot_dot_slash_escape_rejected(tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir()
    (tmp_path / "secret.txt").write_text("leak")
    adapter = FileSystemDossierAdapter(dossier)

    with pytest.raises(ValueError, match="escapes dossier root"):
        adapter.read_text("../secret.txt")


def test_absolute_path_rejected(tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir()
    adapter = FileSystemDossierAdapter(dossier)

    with pytest.raises(ValueError, match="Absolute paths"):
        adapter.read_text("/etc/passwd")


def test_backslash_is_filename_not_separator(tmp_path: Path) -> None:
    # On Unix, backslash is just a filename character.
    dossier = tmp_path / "box"
    dossier.mkdir()
    (dossier / "..\\..\\secret.txt").write_text("backslash trick")
    adapter = FileSystemDossierAdapter(dossier)
    assert adapter.read_text("..\\..\\secret.txt") == "backslash trick"


def test_null_byte_rejected(tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir()
    adapter = FileSystemDossierAdapter(dossier)
    # Pathlib/ OS will reject embedded nulls; we just need a clear failure.
    with pytest.raises((ValueError, TypeError, OSError)):
        adapter.read_text("good.txt\0../evil")


def test_fullwidth_slash_treated_as_regular_char(tmp_path: Path) -> None:
    # U+FF0F fullwidth solidus is not a path separator on Unix.
    dossier = tmp_path / "box"
    dossier.mkdir()
    name = "a\uff0f..\uff0fetc"
    (dossier / name).write_text("no escape")
    adapter = FileSystemDossierAdapter(dossier)
    assert adapter.read_text(name) == "no escape"


# --------------------------------------------------------------------------- #
# Symlink attacks
# --------------------------------------------------------------------------- #


def test_symlink_to_absolute_outside_rejected(tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir()
    (dossier / "escape").symlink_to("/etc/passwd")
    adapter = FileSystemDossierAdapter(dossier)

    with pytest.raises(ValueError, match="escapes dossier root"):
        adapter.read_text("escape")


def test_symlink_chain_outside_rejected(tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir()
    (tmp_path / "target.txt").write_text("outside")
    (dossier / "mid").symlink_to("..")  # points to parent of dossier
    adapter = FileSystemDossierAdapter(dossier)

    with pytest.raises(ValueError, match="escapes dossier root"):
        adapter.read_text("mid/target.txt")


def test_recursive_symlink_does_not_hang(tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir()
    loop = dossier / "loop"
    loop.symlink_to("loop")
    adapter = FileSystemDossierAdapter(dossier)

    # Python's resolve() raises RuntimeError for symlink loops.
    with pytest.raises((OSError, ValueError, RuntimeError, RecursionError)):
        adapter.read_text("loop")


# --------------------------------------------------------------------------- #
# list_files pattern traversal
# --------------------------------------------------------------------------- #


def test_list_files_pattern_dot_dot_rejected(tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir()
    (tmp_path / "secret.lst").write_text("leak")
    adapter = FileSystemDossierAdapter(dossier)

    with pytest.raises(ValueError, match="Pattern must not"):
        adapter.list_files(".", "../*.lst")


def test_list_files_pattern_absolute_rejected(tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir()
    adapter = FileSystemDossierAdapter(dossier)

    with pytest.raises(ValueError, match="Pattern must not"):
        adapter.list_files(".", "/etc/*")


def test_list_files_pattern_via_symlink_escape_rejected(tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir()
    sub = dossier / "sub"
    sub.mkdir()
    (sub / "link").symlink_to("../../../")  # resolves outside dossier
    adapter = FileSystemDossierAdapter(dossier)

    with pytest.raises(ValueError, match="Pattern escapes dossier root"):
        adapter.list_files("sub", "link/*")


def test_list_files_pattern_double_star_rejected(tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir()
    adapter = FileSystemDossierAdapter(dossier)

    with pytest.raises(ValueError, match="Pattern must not"):
        adapter.list_files(".", "**/*")


def test_list_files_double_star_symlink_cycle_does_not_hang(tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir()
    (dossier / "loop").symlink_to(".")
    adapter = FileSystemDossierAdapter(dossier)

    # Must reject ** patterns before glob() can recurse infinitely.
    with pytest.raises(ValueError, match="Pattern must not"):
        adapter.list_files(".", "**/*")


# --------------------------------------------------------------------------- #
# Resource exhaustion & crash resistance
# --------------------------------------------------------------------------- #


def test_extremely_long_path_rejected(tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir()
    adapter = FileSystemDossierAdapter(dossier)

    with pytest.raises((ValueError, OSError)):
        adapter.read_text("a" * 1_000_000)


def test_deeply_nested_directory_read(tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir()
    current = dossier
    depth = 50
    for _ in range(depth):
        current = current / "sub"
        current.mkdir()
    (current / "deep.txt").write_text("deep")

    adapter = FileSystemDossierAdapter(dossier)
    rel = "/".join(["sub"] * depth + ["deep.txt"])
    assert adapter.read_text(rel) == "deep"


def test_symlink_to_overlong_target(tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir()
    # Stay under Linux's symlink target limit (4096) but point to a path that
    # does not exist, so resolution fails cleanly.
    big = "x" * 3000
    (dossier / "big").symlink_to(big)
    adapter = FileSystemDossierAdapter(dossier)

    # Should fail cleanly, not crash.
    with pytest.raises((OSError, ValueError)):
        adapter.read_text("big")


# --------------------------------------------------------------------------- #
# Race / TOCTOU
# --------------------------------------------------------------------------- #


def test_race_file_swap_with_outside_symlink(tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir()
    target = dossier / "target.txt"
    target.write_text("original")
    outside = tmp_path / "outside.txt"
    outside.write_text("hijack")
    adapter = FileSystemDossierAdapter(dossier)

    def attacker() -> None:
        for _ in range(200):
            target.unlink(missing_ok=True)
            target.symlink_to("../outside.txt")
            time.sleep(0.001)
            target.unlink(missing_ok=True)
            target.write_text("original")

    seen: list[str] = []
    t = threading.Thread(target=attacker, daemon=True)
    t.start()
    for _ in range(200):
        with contextlib.suppress(ValueError, FileNotFoundError, OSError):
            seen.append(adapter.read_text("target.txt"))
    t.join(timeout=2)

    # We may see "original" or transient errors, but never the outside content.
    assert "hijack" not in seen


# --------------------------------------------------------------------------- #
# Special files
# --------------------------------------------------------------------------- #


@pytest.mark.timeout(2)
def test_fifo_read_does_not_hang(tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir()
    fifo = dossier / "pipe"
    os.mkfifo(fifo)
    adapter = FileSystemDossierAdapter(dossier)

    def writer() -> None:
        # Wait a tiny moment for the reader to open, then write.
        time.sleep(0.05)
        with open(fifo, "wb") as f:
            f.write(b"fifo-data")

    threading.Thread(target=writer, daemon=True).start()
    assert adapter.read_bytes("pipe") == b"fifo-data"


# --------------------------------------------------------------------------- #
# Known limitations
# --------------------------------------------------------------------------- #


def test_hard_link_inside_dossier_can_leak_outside_file(tmp_path: Path) -> None:
    """Hard links share an inode and do not traverse the path boundary.

    This demonstrates a known limitation: the adapter cannot block a hard link
    placed inside the dossier that points to a file on the same filesystem.
    """
    dossier = tmp_path / "box"
    dossier.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("danger")
    (dossier / "link").hardlink_to(outside)
    adapter = FileSystemDossierAdapter(dossier)

    # Path stays inside the dossier, so the adapter permits the read.
    assert adapter.read_text("link") == "danger"


# --------------------------------------------------------------------------- #
# Hypothesis fuzzing with security invariant
# --------------------------------------------------------------------------- #


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=200)
@given(path=st.text(min_size=0, max_size=500))
def test_fuzz_read_text_security_invariant(path: str, tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir(exist_ok=True)
    (dossier / "known").write_text("KNOWN")
    adapter = FileSystemDossierAdapter(dossier)

    try:
        content = adapter.read_text(path)
    except (ValueError, FileNotFoundError, OSError, UnicodeDecodeError):
        # Rejection or unreadable content is an acceptable defence outcome.
        return

    # If any content was returned, the resolved path must be inside the root.
    resolved = (dossier / path).resolve(strict=False)
    assert resolved.is_relative_to(
        dossier.resolve()
    ), f"Fuzz escaped: {path!r} -> {resolved} (got {content!r})"


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=200)
@given(path=st.text(min_size=0, max_size=500))
def test_fuzz_read_bytes_security_invariant(path: str, tmp_path: Path) -> None:
    dossier = tmp_path / "box"
    dossier.mkdir(exist_ok=True)
    (dossier / "known").write_bytes(b"KNOWN")
    adapter = FileSystemDossierAdapter(dossier)

    try:
        content = adapter.read_bytes(path)
    except (ValueError, FileNotFoundError, OSError):
        return

    resolved = (dossier / path).resolve(strict=False)
    assert resolved.is_relative_to(
        dossier.resolve()
    ), f"Fuzz escaped: {path!r} -> {resolved} (got {content!r})"


# --------------------------------------------------------------------------- #
# OS-specific stubs (skip on non-target platforms)
# --------------------------------------------------------------------------- #


def test_windows_reserved_name(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows-only test")
    dossier = tmp_path / "box"
    dossier.mkdir()
    adapter = FileSystemDossierAdapter(dossier)
    with pytest.raises((OSError, ValueError)):
        adapter.read_text("CON")


def test_case_insensitive_traversal_blocked(tmp_path: Path) -> None:
    if os.name != "nt" and not _is_case_insensitive_fs(tmp_path):
        pytest.skip("Case-sensitive filesystem")
    dossier = tmp_path / "box"
    dossier.mkdir()
    (tmp_path / "SECRET").write_text("leak")
    adapter = FileSystemDossierAdapter(dossier)
    with pytest.raises(ValueError, match="escapes dossier root"):
        adapter.read_text("../secret")


def test_long_path_windows(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows-only test")
    dossier = tmp_path / "box"
    dossier.mkdir()
    adapter = FileSystemDossierAdapter(dossier)
    long_rel = "a" * 300 + "/file.txt"
    with pytest.raises((OSError, ValueError)):
        adapter.read_text(long_rel)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _is_case_insensitive_fs(path: Path) -> bool:
    """Best-effort check for case-insensitive filesystem behavior."""
    lower = path / "casecheck_lower"
    upper = path / "CASECHECK_LOWER"
    try:
        lower.write_text("x")
        return upper.exists()
    finally:
        lower.unlink(missing_ok=True)
        upper.unlink(missing_ok=True)
