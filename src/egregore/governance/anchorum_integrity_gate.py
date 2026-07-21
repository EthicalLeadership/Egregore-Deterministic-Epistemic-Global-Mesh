"""
ANCHORUM Phase-1 Integrity Gate
Verifies critical invariants before any deploy or update cycle.

Configuration is read from environment variables so the same gate can run in
production, on a developer workstation, or in CI without source edits.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from egregore.shared.canonical import canonical_loads
from egregore.shared.freeze_state import FreezeController

logger = logging.getLogger(__name__)


class AnchorumIntegrityFailure(Exception):  # noqa: N818
    """Raised when one or more integrity checks fail."""

    report: dict[str, Any]


@dataclass(frozen=True)
class AnchorumConfig:
    """Runtime configuration for the integrity gate."""

    egregore_root: Path
    recovered_downloads_dir: Path
    dedup_log: Path
    backup_dir: Path
    db_dsn: str
    ci_mode: bool
    use_sudo: bool
    backup_max_age_hours: float
    critical_paths: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> AnchorumConfig:
        """Build a config from environment variables."""
        repo_root = cls._resolve_repo_root()

        recovered_default = Path(
            os.environ.get(
                "BLACKSTAR_RECOVERED_DOWNLOADS_DIR",
                os.environ.get("RECOVERED_DOWNLOADS_DIR", "/opt/egregore/recovered_downloads"),
            )
        )
        dedup_default = Path(
            os.environ.get(
                "BLACKSTAR_DEDUP_LOG",
                "/opt/egregore/hardening/dedup_log.json",
            )
        )
        backup_default = Path(
            os.environ.get("BLACKSTAR_BACKUP_DIR", "/opt/egregore/backup")
        )

        return cls(
            egregore_root=repo_root,
            recovered_downloads_dir=recovered_default,
            dedup_log=dedup_default,
            backup_dir=backup_default,
            db_dsn=os.environ.get(
                "BLACKSTAR_DB_DSN",
                "postgresql://egregore:testpass@localhost:5432/egregore_test",
            ),
            ci_mode=os.environ.get("ANCHORUM_CI", "").lower() in ("1", "true", "yes"),
            use_sudo=os.environ.get("ANCHORUM_USE_SUDO", "true").lower()
            in ("1", "true", "yes"),
            backup_max_age_hours=float(
                os.environ.get("ANCHORUM_BACKUP_MAX_AGE_HOURS", "48")
            ),
            critical_paths=_parse_critical_paths(),
        )

    @property
    def src_dir(self) -> Path:
        return self.egregore_root / "src"

    @staticmethod
    def _resolve_repo_root() -> Path:
        """Return the repository root, preferring BLACKSTAR_ROOT if set."""
        if env_root := os.environ.get("BLACKSTAR_ROOT"):
            return Path(env_root).expanduser().resolve()
        # src/egregore/governance/anchorum_integrity_gate.py -> 4 levels up
        return Path(__file__).resolve().parents[3]


def _parse_critical_paths() -> list[str]:
    """Return the list of paths that must exist and be non-empty."""
    env_paths = os.environ.get("ANCHORUM_CRITICAL_PATHS")
    if env_paths:
        return [p.strip() for p in env_paths.split(",") if p.strip()]
    return [
        "egregore/domain/models/dossier.py",
        "egregore/domain/models/event.py",
        "egregore/infrastructure/adapters/postgresql_persistence.py",
        "egregore/governance/dag_signer.py",
        "egregore/kernel/provenance.py",
        "egregore/interface/semantics_ports.py",
        "egregore/application/dossier_generate_service.py",
    ]


def _record(
    report: dict[str, Any],
    name: str,
    status: str,
    error: str | None,
    warning: str | None,
) -> None:
    """Record a single check result, separating fatal errors from warnings."""
    report["checks"][name] = status
    if error:
        report["errors"].append(f"{name}: {error}")
    if warning:
        report["warnings"].append(f"{name}: {warning}")


def _check_files(config: AnchorumConfig) -> tuple[str, str | None, str | None]:
    """Verify critical source files exist and are non-empty."""
    errors: list[str] = []
    for rel in config.critical_paths:
        p = config.src_dir / rel
        if not p.exists():
            errors.append(f"MISSING: {rel}")
        elif p.stat().st_size == 0:
            errors.append(f"EMPTY: {rel}")
    if errors:
        return "FAIL", "; ".join(errors), None
    return "PASS", None, None


def _check_imports(
    config: AnchorumConfig,
    required: list[tuple[str, list[str]]] | None = None,
) -> tuple[str, str | None, str | None]:
    """Statically verify required modules and attributes are importable."""
    if required is None:
        required = [
            (
                "egregore.domain.models.dossier",
                ["Dossier", "DossierState", "CommitResult"],
            ),
            ("egregore.domain.models.event", ["Event"]),
            (
                "egregore.infrastructure.adapters.postgresql_persistence",
                ["PostgreSQLPersistence", "PersistenceConfig"],
            ),
            ("egregore.governance.dag_signer", []),
            ("egregore.kernel.provenance", []),
        ]
    errors: list[str] = []
    for mod_name, attrs in required:
        try:
            spec = importlib.util.find_spec(mod_name)
        except Exception:
            spec = None
        if spec is None:
            errors.append(f"{mod_name} not found")
            continue
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"{mod_name} import failed: {exc}")
            continue
        for attr in attrs:
            if not hasattr(mod, attr):
                errors.append(f"{mod_name}.{attr} missing")
    if errors:
        return "FAIL", "; ".join(errors), None
    return "PASS", None, None


def _check_postgresql(config: AnchorumConfig) -> tuple[str, str | None, str | None]:
    """Lightweight PostgreSQL connectivity probe using the configured DSN."""
    try:
        import psycopg
    except ImportError as exc:
        return "FAIL", f"psycopg not installed: {exc}", None

    try:
        # Fast path: pg_isready if available.
        pg_isready = Path("/usr/bin/pg_isready")
        if pg_isready.exists():
            result = subprocess.run(  # noqa: S603
                [str(pg_isready), "-d", config.db_dsn],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return "FAIL", f"pg_isready failed: {result.stdout.strip()}", None

        # Confirm with an actual short-lived connection.
        conn = psycopg.connect(config.db_dsn, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        conn.close()
        return "PASS", None, None
    except Exception as exc:
        return "FAIL", str(exc), None


def _check_domain_models(
    config: AnchorumConfig,
) -> tuple[str, str | None, str | None]:
    """Instantiate a minimal Dossier and exercise its event state."""
    try:
        dossier_mod = importlib.import_module("egregore.domain.models.dossier")
        event_mod = importlib.import_module("egregore.domain.models.event")
        dossier_cls = dossier_mod.Dossier
        dossier_state_cls = dossier_mod.DossierState
        event_cls = event_mod.Event

        event = event_cls(
            event_type="anchorum.check", payload={"ok": True}, timestamp_ns=1
        )
        dossier = dossier_cls(
            dossier_id="anchorum-check",
            case_id="anchorum",
            version=1,
            intent_hash="check",
            state=dossier_state_cls(events=[event]),
            canonical_state='{"events":[]}',
            timestamp_ns=1,
            signature="check",
        )
        if dossier.dossier_id != "anchorum-check":
            return "FAIL", "dossier_id mismatch", None
        state_dict = dossier.state.to_dict()
        if len(state_dict["events"]) != 1:
            return "FAIL", "event count mismatch", None
        if state_dict["events"][0]["event_type"] != "anchorum.check":
            return "FAIL", "event type mismatch", None
        return "PASS", None, None
    except Exception as exc:
        return "FAIL", str(exc), None


def _check_kek(config: AnchorumConfig) -> tuple[str, str | None, str | None]:
    """Delegate to the cluster KEK health check."""
    try:
        kek_mod = importlib.import_module("egregore.infrastructure.cluster_kek")
        kek_health = kek_mod.kek_health_check()
        status = kek_health["status"]
        if status != "HEALTHY":
            return status, kek_health.get("error", status), None
        return "PASS", None, None
    except Exception as exc:
        return "FAIL", str(exc), None


def _check_gguf(config: AnchorumConfig) -> tuple[str, str | None, str | None]:
    """Delegate to the GGUF catalog health check."""
    try:
        gguf_mod = importlib.import_module("egregore.infrastructure.gguf_catalog")
        gguf_health = gguf_mod.run_gguf_health_check()
        status = gguf_health["status"]
        if status != "HEALTHY":
            return status, gguf_health.get("error", status), None
        return "PASS", None, None
    except Exception as exc:
        return "FAIL", str(exc), None


def _load_dedup_log(log: Path) -> tuple[Any, str | None]:
    """Load and return the dedup log payload, or an error message."""
    try:
        return canonical_loads(log.read_text()), None
    except json.JSONDecodeError as exc:
        return None, f"dedup log is not valid JSON: {exc}"
    except Exception as exc:
        return None, str(exc)


def _validate_dedup_list(
    records: list[Any], zips: list[Path]
) -> tuple[str, str | None, str | None]:
    """Validate a dedup log stored as a list of run records."""
    if not records:
        return "FAIL", "dedup log list is empty", None
    latest = records[-1]
    if not isinstance(latest, dict):
        return "FAIL", "latest dedup log entry is not an object", None
    expected = {"timestamp", "total_zips", "unique_hashes", "duplicates_found"}
    missing_fields = expected - set(latest.keys())
    if missing_fields:
        return (
            "FAIL",
            f"latest dedup log entry missing fields: {', '.join(sorted(missing_fields))}",
            None,
        )
    if zips and latest.get("total_zips") != len(zips):
        return (
            "WARN",
            None,
            f"dedup log total_zips ({latest.get('total_zips')}) does not match "
            f"current archives ({len(zips)})",
        )
    return "PASS", None, None


def _validate_dedup_dict(
    raw: dict[str, Any], zips: list[Path]
) -> tuple[str, str | None, str | None]:
    """Validate a dedup log stored as filename -> entry mapping."""
    seen_hashes: dict[str, str] = {}
    missing: list[str] = []
    duplicates: list[str] = []

    for zip_path in zips:
        key = zip_path.name
        entry = raw.get(key)
        if not isinstance(entry, dict):
            missing.append(key)
            continue
        sha = entry.get("sha256")
        if not sha:
            missing.append(key)
            continue
        if sha in seen_hashes:
            duplicates.append(f"{key} <=> {seen_hashes[sha]}")
        else:
            seen_hashes[sha] = key

    if duplicates:
        return "FAIL", f"duplicate archives detected: {', '.join(duplicates)}", None
    if missing:
        return "FAIL", f"archives missing from log: {', '.join(missing)}", None
    return "PASS", None, None


def _check_dedup(config: AnchorumConfig) -> tuple[str, str | None, str | None]:
    """Validate the downloads deduplication manifest, if present."""
    dl_dir = config.recovered_downloads_dir
    zips = list(dl_dir.glob("*.zip")) if dl_dir.exists() else []
    log = config.dedup_log

    if not log.exists():
        if zips:
            return "WARN", None, f"dedup log missing for {len(zips)} archive(s)"
        return "PASS", None, None

    raw, error = _load_dedup_log(log)
    if error:
        return "FAIL", error, None

    if isinstance(raw, list):
        return _validate_dedup_list(raw, zips)
    if isinstance(raw, dict):
        return _validate_dedup_dict(raw, zips)
    return "FAIL", "dedup log root is not a JSON object or array", None


def _run_command(
    args: list[str],
    config: AnchorumConfig,
    timeout: float = 5.0,
) -> subprocess.CompletedProcess[str]:
    """Run a command, optionally escalating to sudo on permission failure."""
    result = subprocess.run(  # noqa: S603
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0 and config.use_sudo and args[0] != "sudo":
        sudo_result = subprocess.run(  # noqa: S603
            ["sudo"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if sudo_result.returncode == 0:
            return sudo_result
    return result


def _check_ufw(config: AnchorumConfig) -> tuple[str, str | None, str | None]:
    """Check that UFW is active. Skipped in CI mode."""
    if config.ci_mode:
        return "SKIP", None, None
    result = _run_command(["ufw", "status"], config, timeout=5)
    if result.returncode == 0 and "Status: active" in result.stdout:
        return "PASS", None, None
    # Fallback: UFW service state + config file (works without sudo).
    try:
        ufw_conf = Path("/etc/ufw/ufw.conf")
        if ufw_conf.exists():
            enabled = any(
                line.strip() == "ENABLED=yes"
                for line in ufw_conf.read_text().splitlines()
                if line.strip() and not line.strip().startswith("#")
            )
            if enabled:
                svc = subprocess.run(
                    ["systemctl", "is-active", "ufw"],  # noqa: S607
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if svc.returncode == 0 and svc.stdout.strip() == "active":
                    return "PASS", None, None
    except Exception:
        logger.debug("UFW fallback check failed", exc_info=True)
    return "FAIL", "UFW is not active", None


def _check_fail2ban(config: AnchorumConfig) -> tuple[str, str | None, str | None]:
    """Check that the fail2ban sshd jail is active. Skipped in CI mode."""
    if config.ci_mode:
        return "SKIP", None, None
    result = _run_command(["fail2ban-client", "status"], config, timeout=5)
    if result.returncode == 0 and "sshd" in result.stdout:
        return "PASS", None, None
    # Fallback: service state + log evidence (works without sudo for adm group).
    try:
        svc = subprocess.run(
            ["systemctl", "is-active", "fail2ban"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
        )
        if svc.returncode == 0 and svc.stdout.strip() == "active":
            log = Path("/var/log/fail2ban.log")
            if log.exists():
                text = log.read_text(errors="replace")
                if "Jail 'sshd' started" in text or "Jail 'sshd' reloaded" in text:
                    return "PASS", None, None
    except Exception:
        logger.debug("fail2ban fallback check failed", exc_info=True)
    return "WARN", None, "fail2ban sshd jail not active"


def _check_backup(config: AnchorumConfig) -> tuple[str, str | None, str | None]:
    """Check that a recent database backup exists. Skipped in CI mode."""
    if config.ci_mode:
        return "SKIP", None, None
    backup_dir = config.backup_dir
    if not backup_dir.exists():
        return "FAIL", f"Backup directory missing: {backup_dir}", None
    backups = list(backup_dir.glob("egregore_db_*.sql"))
    if not backups:
        return "FAIL", "No database backups found", None
    latest = max(backups, key=lambda p: p.stat().st_mtime)
    age_hours = (time.time() - latest.stat().st_mtime) / 3600
    if age_hours < config.backup_max_age_hours:
        return "PASS", None, None
    return (
        "WARN",
        None,
        f"Backup stale: {age_hours:.1f}h old",
    )


def _check_wireguard(config: AnchorumConfig) -> tuple[str, str | None, str | None]:
    """Check that WireGuard has active interfaces when installed. Skipped in CI."""
    if config.ci_mode:
        return "SKIP", None, None
    if shutil.which("wg") is None:
        return "SKIP", None, None
    result = _run_command(["wg", "show"], config, timeout=3)
    if result.returncode == 0 and result.stdout.strip():
        return "PASS", None, None
    # Fallback: inspect kernel network interface state (works without sudo).
    try:
        ip = subprocess.run(
            ["ip", "link", "show", "wg0"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=3,
        )
        if ip.returncode == 0 and "UP" in ip.stdout:
            return "PASS", None, None
    except Exception:
        logger.debug("WireGuard fallback check failed", exc_info=True)
    return "WARN", None, "WireGuard installed but no active interfaces"


CHECKS: list[tuple[str, Any]] = [
    ("files", _check_files),
    ("imports", _check_imports),
    ("postgresql", _check_postgresql),
    ("domain_models", _check_domain_models),
    ("kek", _check_kek),
    ("gguf", _check_gguf),
    ("dedup", _check_dedup),
    ("ufw", _check_ufw),
    ("fail2ban", _check_fail2ban),
    ("backup", _check_backup),
    ("wireguard", _check_wireguard),
]


def run_anchorum_check(
    config: AnchorumConfig | None = None,
    freeze_controller: FreezeController | None = None,
) -> dict[str, Any]:
    """Run all ANCHORUM integrity checks.

    Args:
        config: Optional explicit configuration. If omitted, configuration is
            loaded from environment variables.
        freeze_controller: Optional freeze controller to notify on failure.

    Returns:
        A report dict with ``status``, ``checks``, ``errors``, and ``warnings``.

    Raises:
        AnchorumIntegrityFailure: If any check reports a fatal error.

    """
    if config is None:
        config = AnchorumConfig.from_env()

    report: dict[str, Any] = {
        "status": "PASS",
        "checks": {},
        "errors": [],
        "warnings": [],
    }

    for name, check in CHECKS:
        try:
            status, error, warning = check(config)
        except Exception as exc:
            status, error, warning = "FAIL", str(exc), None
            logger.exception("Unexpected failure in %s check", name)
        _record(report, name, status, error, warning)

    if report["errors"]:
        report["status"] = "FAIL"
    elif report["warnings"]:
        report["status"] = "WARN"
    else:
        report["status"] = "PASS"

    if report["errors"]:
        reason = (
            f"ANCHORUM FAIL: {len(report['errors'])} error(s) — "
            f"{report['errors'][0]}"
        )
        if freeze_controller is not None:
            if hasattr(freeze_controller, "integrity_breach"):
                freeze_controller.integrity_breach(
                    reason=reason,
                    timestamp_ns=time.time_ns(),
                    detection_source="anchorum_integrity_gate",
                )
            else:
                freeze_controller.fork_detected(
                    reason=reason,
                    timestamp_ns=time.time_ns(),
                    detection_source="anchorum_integrity_gate",
                )
        exc = AnchorumIntegrityFailure(reason)
        exc.report = report
        raise exc

    return report


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ANCHORUM Phase-1 pre-deployment integrity gate",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="Skip checks that require elevated privileges or a production layout.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    if args.ci:
        os.environ.setdefault("ANCHORUM_CI", "true")

    try:
        result = run_anchorum_check()
        logger.info("ANCHORUM_OK: %s", result["checks"])
        return 0
    except AnchorumIntegrityFailure as exc:
        logger.error("ANCHORUM_FAIL: %s", exc)
        logger.error("Report: %s", exc.report)
        return 1


if __name__ == "__main__":
    sys.exit(main())
