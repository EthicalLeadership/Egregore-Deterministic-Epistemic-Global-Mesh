"""Tests for the ANCHORUM integrity gate."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from egregore.governance import anchorum_integrity_gate as gate
from egregore.governance.anchorum_integrity_gate import (
    AnchorumConfig,
    AnchorumIntegrityFailure,
    run_anchorum_check,
)
from egregore.shared.freeze_state import FreezeController


def _make_config(tmp_path: Path, **overrides: Any) -> AnchorumConfig:
    defaults = {
        "egregore_root": tmp_path,
        "recovered_downloads_dir": tmp_path / "downloads",
        "dedup_log": tmp_path / "dedup_log.json",
        "backup_dir": tmp_path / "backup",
        "db_dsn": "postgresql://test:test@localhost/test",
        "ci_mode": False,
        "use_sudo": False,
        "backup_max_age_hours": 48.0,
        "critical_paths": ["dummy.py"],
    }
    defaults.update(overrides)
    return AnchorumConfig(**defaults)


def test_config_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EGREGORE_ROOT", str(tmp_path))
    monkeypatch.setenv("EGREGORE_DB_DSN", "postgresql://env/env")
    monkeypatch.setenv("ANCHORUM_CI", "true")
    monkeypatch.setenv("ANCHORUM_BACKUP_MAX_AGE_HOURS", "12")
    cfg = AnchorumConfig.from_env()
    assert cfg.egregore_root == tmp_path
    assert cfg.db_dsn == "postgresql://env/env"
    assert cfg.ci_mode is True
    assert cfg.backup_max_age_hours == 12.0


def test_check_files_pass(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "dummy.py").write_text("print('ok')")
    cfg = _make_config(tmp_path, egregore_root=tmp_path, critical_paths=["dummy.py"])
    status, error, warning = gate._check_files(cfg)
    assert status == "PASS"
    assert error is None


def test_check_files_missing_and_empty(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "empty.py").write_text("")
    cfg = _make_config(
        tmp_path,
        egregore_root=tmp_path,
        critical_paths=["missing.py", "empty.py"],
    )
    status, error, warning = gate._check_files(cfg)
    assert status == "FAIL"
    assert "MISSING: missing.py" in error
    assert "EMPTY: empty.py" in error


def test_check_imports_pass(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    status, error, warning = gate._check_imports(cfg)
    assert status == "PASS"


def test_check_imports_missing_module(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    status, error, warning = gate._check_imports(
        cfg,
        required=[("egregore.nonexistent.module_xyz", ["Foo"])],
    )
    assert status == "FAIL"
    assert "not found" in error


def test_check_imports_missing_attr(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    status, error, warning = gate._check_imports(
        cfg,
        required=[("egregore.domain.models.dossier", ["NotARealAttr"])],
    )
    assert status == "FAIL"
    assert "NotARealAttr missing" in error


def test_check_domain_models_pass(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    status, error, warning = gate._check_domain_models(cfg)
    assert status == "PASS"


def test_check_postgresql_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pytest.importorskip("psycopg")
    cfg = _make_config(tmp_path)
    calls: list[Any] = []

    class FakeCursor:
        def execute(self, sql: str) -> None:
            calls.append(sql)

        def fetchone(self) -> tuple[int]:
            return (1,)

        def close(self) -> None:
            pass

    class FakeConn:
        def cursor(self) -> FakeCursor:
            return FakeCursor()

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "psycopg.connect",
        lambda dsn, connect_timeout: FakeConn(),
    )
    status, error, warning = gate._check_postgresql(cfg)
    assert status == "PASS"


def test_check_postgresql_missing_psycopg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _make_config(tmp_path)
    monkeypatch.setitem(gate.sys.modules, "psycopg", None)  # type: ignore[arg-type]
    monkeypatch.setattr(
        gate.importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(ImportError("no psycopg")),
    )
    status, error, warning = gate._check_postgresql(cfg)
    assert status == "FAIL"
    assert "psycopg not installed" in error


def test_check_dedup_no_archives_no_log(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    cfg.recovered_downloads_dir.mkdir(parents=True, exist_ok=True)
    status, error, warning = gate._check_dedup(cfg)
    assert status == "PASS"


def test_check_dedup_missing_log_warns(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    cfg.recovered_downloads_dir.mkdir(parents=True, exist_ok=True)
    (cfg.recovered_downloads_dir / "a.zip").write_text("data")
    status, error, warning = gate._check_dedup(cfg)
    assert status == "WARN"
    assert "dedup log missing" in warning


def test_check_dedup_valid_log_passes(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    cfg.recovered_downloads_dir.mkdir(parents=True, exist_ok=True)
    (cfg.recovered_downloads_dir / "a.zip").write_text("data")
    cfg.dedup_log.write_text(
        json.dumps({"a.zip": {"sha256": "abc123", "deduped_at": "now"}})
    )
    status, error, warning = gate._check_dedup(cfg)
    assert status == "PASS"


def test_check_dedup_duplicate_hashes_fail(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    cfg.recovered_downloads_dir.mkdir(parents=True, exist_ok=True)
    (cfg.recovered_downloads_dir / "a.zip").write_text("data")
    (cfg.recovered_downloads_dir / "b.zip").write_text("data")
    cfg.dedup_log.write_text(
        json.dumps(
            {
                "a.zip": {"sha256": "same", "deduped_at": "now"},
                "b.zip": {"sha256": "same", "deduped_at": "now"},
            }
        )
    )
    status, error, warning = gate._check_dedup(cfg)
    assert status == "FAIL"
    assert "duplicate archives detected" in error


def test_check_dedup_missing_entry_fail(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    cfg.recovered_downloads_dir.mkdir(parents=True, exist_ok=True)
    (cfg.recovered_downloads_dir / "a.zip").write_text("data")
    cfg.dedup_log.write_text(json.dumps({}))
    status, error, warning = gate._check_dedup(cfg)
    assert status == "FAIL"
    assert "archives missing from log" in error


def test_check_backup_fresh_pass(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    (cfg.backup_dir / "egregore_db_001.sql").write_text("dump")
    status, error, warning = gate._check_backup(cfg)
    assert status == "PASS"


def test_check_backup_stale_warns(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path, backup_max_age_hours=1.0)
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    backup = cfg.backup_dir / "egregore_db_001.sql"
    backup.write_text("dump")
    old_mtime = time.time() - 7200
    os.utime(backup, (old_mtime, old_mtime))
    status, error, warning = gate._check_backup(cfg)
    assert status == "WARN"
    assert "Backup stale" in warning


def test_check_backup_no_backups_fail(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    status, error, warning = gate._check_backup(cfg)
    assert status == "FAIL"
    assert "No database backups found" in error


def test_ci_mode_skips_host_checks(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path, ci_mode=True)
    for check in (
        gate._check_ufw,
        gate._check_fail2ban,
        gate._check_backup,
        gate._check_wireguard,
    ):
        status, error, warning = check(cfg)
        assert status == "SKIP", f"{check.__name__} did not skip in CI mode"


def test_run_anchorum_check_fail_aggregates_errors_and_freezes(
    tmp_path: Path,
) -> None:
    cfg = _make_config(tmp_path, critical_paths=["missing.py"])
    fc = FreezeController()
    with pytest.raises(AnchorumIntegrityFailure) as exc_info:
        run_anchorum_check(config=cfg, freeze_controller=fc)
    report = exc_info.value.report
    assert report["status"] == "FAIL"
    assert any("missing.py" in e for e in report["errors"])
    assert fc.is_frozen is True
    assert fc.history[-1].detection_source == "anchorum_integrity_gate"


def test_run_anchorum_check_warning_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _make_config(tmp_path, ci_mode=False)

    # Replace the check registry so only one emits a warning; no errors occur.
    monkeypatch.setattr(
        gate,
        "CHECKS",
        [
            ("files", lambda c: ("PASS", None, None)),
            ("imports", lambda c: ("PASS", None, None)),
            ("postgresql", lambda c: ("PASS", None, None)),
            ("domain_models", lambda c: ("PASS", None, None)),
            ("kek", lambda c: ("PASS", None, None)),
            ("gguf", lambda c: ("PASS", None, None)),
            ("dedup", lambda c: ("WARN", None, "example warning")),
            ("ufw", lambda c: ("SKIP", None, None)),
            ("fail2ban", lambda c: ("SKIP", None, None)),
            ("backup", lambda c: ("SKIP", None, None)),
            ("wireguard", lambda c: ("SKIP", None, None)),
        ],
    )

    report = run_anchorum_check(config=cfg)
    assert report["status"] == "WARN"
    assert report["warnings"]
    assert not report["errors"]


def test_main_ci_flag_runs_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # main() sets ANCHORUM_CI=true when --ci is passed. Replace the check
    # registry so the test does not depend on the host environment.
    monkeypatch.setattr(
        gate,
        "CHECKS",
        [
            ("files", lambda c: ("PASS", None, None)),
            ("imports", lambda c: ("PASS", None, None)),
            ("postgresql", lambda c: ("PASS", None, None)),
            ("domain_models", lambda c: ("PASS", None, None)),
            ("kek", lambda c: ("PASS", None, None)),
            ("gguf", lambda c: ("PASS", None, None)),
            ("dedup", lambda c: ("PASS", None, None)),
            ("ufw", lambda c: ("SKIP", None, None)),
            ("fail2ban", lambda c: ("SKIP", None, None)),
            ("backup", lambda c: ("SKIP", None, None)),
            ("wireguard", lambda c: ("SKIP", None, None)),
        ],
    )
    assert gate.main(["--ci"]) == 0
