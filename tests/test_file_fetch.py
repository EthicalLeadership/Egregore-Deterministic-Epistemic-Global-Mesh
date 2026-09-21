"""Headless tests for file_fetch pure logic (consent ledger, probe, staging).

Tk widgets are intentionally not tested here — no display in CI/sandbox.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from file_fetch import (  # noqa: E402
    ConsentLedger,
    derive_staging_dir,
    probe_paths,
    read_for_clipboard,
    scan_partitions,
    stage_fetch,
)


@pytest.fixture()
def ledger(tmp_path: Path) -> ConsentLedger:
    return ConsentLedger(tmp_path / "ledger.jsonl")


# --------------------------------------------------------------------- ledger
def test_ledger_chain_intact(ledger: ConsentLedger) -> None:
    ledger.append("request", paths=["/a"], case="C1")
    ledger.append("grant_once", paths=["/a"], case="C1")
    ledger.append("fetch_complete", case="C1", file_count=1)
    intact, count, broken_at = ledger.verify()
    assert intact and count == 3 and broken_at is None
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    hashes = {json.loads(line)["hash"] for line in lines}
    assert len(hashes) == 3  # hashes actually differ per entry


def test_ledger_tamper_detected_at_exact_entry(ledger: ConsentLedger) -> None:
    ledger.append("request", paths=["/safe/a"], case="C1")
    ledger.append("grant_once", paths=["/safe/b"], case="C1")
    ledger.append("fetch_complete", case="C1", file_count=1)

    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    entry2 = json.loads(lines[1])
    entry2["paths"] = ["/etc/shadow"]  # tamper with the record
    lines[1] = json.dumps(entry2, sort_keys=True)
    ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    intact, _count, broken_at = ledger.verify()
    assert not intact
    assert broken_at == 2


def test_ledger_empty_is_intact(ledger: ConsentLedger) -> None:
    assert ledger.verify() == (True, 0, None)


# --------------------------------------------------------------------- probe
def test_probe_counts_and_errors(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"x" * 100)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_bytes(b"y" * 50)
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "secret.txt").write_bytes(b"z")
    locked.chmod(0o000)

    try:
        probe = probe_paths([tmp_path])
    finally:
        locked.chmod(0o700)  # let pytest clean up

    if os.geteuid() == 0:
        pytest.skip("root ignores permission bits")
    assert probe.file_count == 2
    assert probe.total_bytes == 150
    assert probe.errors and "locked" in probe.errors[0]


# ------------------------------------------------------------------- staging
def test_staging_dirs_unique_per_source_root(tmp_path: Path) -> None:
    d1 = derive_staging_dir("C1", Path("/mnt/disk-a/docs"), "20260809T000000Z", tmp_path)
    d2 = derive_staging_dir("C1", Path("/mnt/disk-b/docs"), "20260809T000000Z", tmp_path)
    assert d1 != d2
    assert "__" in d1.name and "/" not in d1.name
    assert d1.name.startswith("mnt__disk-a__docs")


def test_stage_fetch_manifest_and_hashes(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_bytes(b"alpha")
    (src / "sub" / "b.txt").write_bytes(b"beta")
    staging = tmp_path / "staging"

    manifest = stage_fetch([src], staging, consent_hash="ab" * 32, case="C1")

    assert manifest["file_count"] == 2
    assert manifest["consent_hash"] == "ab" * 32
    assert manifest["errors"] == []
    by_source = {f["source"]: f for f in manifest["files"]}
    a = by_source[str(src / "a.txt")]
    assert a["sha256"] == hashlib.sha256(b"alpha").hexdigest()
    # relative structure preserved under payload/
    assert (staging / "payload" / "src" / "sub" / "b.txt").read_bytes() == b"beta"
    on_disk = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["consent_hash"] == "ab" * 32
    assert on_disk["file_count"] == 2


def test_stage_fetch_collects_errors_not_raises(tmp_path: Path) -> None:
    good = tmp_path / "good.txt"
    good.write_bytes(b"ok")
    missing = tmp_path / "gone.txt"  # stat fails at copy time
    staging = tmp_path / "staging"

    manifest = stage_fetch([good, missing], staging, consent_hash="cd" * 32)

    assert manifest["file_count"] == 1
    assert len(manifest["errors"]) == 1
    assert "gone.txt" in manifest["errors"][0]


# ------------------------------------------------------------------ clipboard
def test_read_for_clipboard_exact(tmp_path: Path) -> None:
    f = tmp_path / "note.txt"
    f.write_text("hello éù\n", encoding="utf-8")
    text, truncated, size = read_for_clipboard(f)
    assert text == "hello éù\n"
    assert truncated is False
    assert size == len("hello éù\n".encode("utf-8"))


def test_read_for_clipboard_caps_with_marker(tmp_path: Path) -> None:
    f = tmp_path / "big.txt"
    f.write_bytes(b"a" * 4096)
    text, truncated, size = read_for_clipboard(f, cap=1024)
    assert truncated is True
    assert size == 4096
    assert text.startswith("a" * 1024)
    assert "truncated" in text and "4096" in text


def test_read_for_clipboard_binary_replaced_not_raised(tmp_path: Path) -> None:
    f = tmp_path / "bin.dat"
    f.write_bytes(b"\xff\xfe\x00\x01abc")
    text, truncated, _size = read_for_clipboard(f)
    assert truncated is False
    assert "abc" in text  # undecodable bytes became U+FFFD, content survived


def test_read_for_clipboard_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        read_for_clipboard(tmp_path / "nope.txt")


# ---------------------------------------------------------------- partitions
MOUNTS_SAMPLE = """\
/dev/nvme0n1p2 / ext4 rw,relatime 0 0
proc /proc proc rw,nosuid,nodev,noexec 0 0
sysfs /sys sysfs rw,nosuid,nodev,noexec 0 0
tmpfs /run tmpfs rw,nosuid,nodev 0 0
/dev/sda1 /mnt/blackstar/vol-hdd-a ext4 rw,relatime 0 0
cgroup2 /sys/fs/cgroup cgroup2 rw,nosuid,nodev,noexec 0 0
/dev/loop0 /snap/core22/1380 squashfs ro,nodev,relatime 0 0
overlay /var/lib/docker/overlay2/x overlay rw,relatime 0 0
portal /run/user/1000/doc fuse.portal rw,nosuid,nodev 0 0
/dev/sda1 /mnt/blackstar/vol-hdd-a ext4 rw,relatime 0 0
"""


def test_scan_partitions_filters_pseudo_fs() -> None:
    parts = scan_partitions(MOUNTS_SAMPLE)
    mounts = [p.mountpoint for p in parts]
    assert mounts[0] == "/"  # root first
    assert "/mnt/blackstar/vol-hdd-a" in mounts
    assert mounts.count("/mnt/blackstar/vol-hdd-a") == 1  # deduped
    for pseudo in ("/proc", "/sys", "/run", "/snap/core22/1380",
                   "/var/lib/docker/overlay2/x", "/run/user/1000/doc"):
        assert pseudo not in mounts
