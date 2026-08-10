#!/usr/bin/env python3
"""Consent-gated IMAP email ingestion for the ANCHORUM desktop app.

Same forensic protocol as ``file_fetch.py``:

1. Default-deny — no message is fetched without an affirmative Accept.
2. Informed scope — the dialog shows the exact account, folders, and message
   counts before anything leaves the server.
3. Three choices — Accept once / Accept for session / Refuse. Session grants
   are scoped to the account, live only in memory, die with the app, and are
   themselves logged.
4. Tamper-evident ledger — every request, grant, refusal, completion, and
   error is appended to a hash-chained JSONL ledger (the ``ConsentLedger``
   from ``file_fetch``; separate file, same chain model).
5. Chain of custody — every fetched message is stored as raw RFC822 ``.eml``
   plus a parsed-metadata ``.json``, each SHA-256'd, plus a job manifest that
   records the ledger hash of the consent that authorized the fetch.

Stdlib only: ``imaplib`` + ``email``. No external dependencies.
"""

from __future__ import annotations

import imaplib
import json
import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import UTC, datetime
from email import message_from_bytes
from email.policy import default as email_policy
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable

from file_fetch import (
    FETCH_ROOT,
    REPO_ROOT,
    ConsentLedger,
    _fmt_bytes,
    _sanitize_component,
    _sha256_file,
    _utc_now,
)

EMAIL_LEDGER_PATH = REPO_ROOT / "email_consent_ledger.jsonl"

# ---------------------------------------------------------------------------
# Pure logic (headless-testable — no Tk below this line)
# ---------------------------------------------------------------------------


@dataclass
class ImapConfig:
    host: str
    port: int = 993
    user: str = ""
    password: str = ""
    use_ssl: bool = True

    @property
    def account(self) -> str:
        return f"{self.user}@{self.host}"


def _connect(config: ImapConfig) -> imaplib.IMAP4:
    cls = imaplib.IMAP4_SSL if config.use_ssl else imaplib.IMAP4
    conn = cls(config.host, config.port)
    conn.login(config.user, config.password)
    return conn


def _parse_list_line(line: bytes) -> str | None:
    """Parse one IMAP LIST response line into a folder name.

    Handles ``(\\HasNoChildren) "/" "INBOX.Sent"`` and unquoted final atoms.
    """
    text = line.decode(errors="replace").strip()
    if not text:
        return None
    if text.count('"') >= 2:
        return text.rsplit('"', 2)[-2]
    return text.split()[-1] if text.split() else None


def list_folders(config: ImapConfig) -> tuple[bool, list[str] | str]:
    """Return ``(True, sorted_folders)`` or ``(False, error)``. Never raises."""
    try:
        conn = _connect(config)
    except Exception as exc:  # noqa: BLE001 — fail-soft by design
        return False, f"connect/login failed: {exc}"
    try:
        typ, data = conn.list()
        if typ != "OK":
            return False, f"IMAP LIST failed: {typ}"
        folders = [n for n in (_parse_list_line(ln) for ln in data or []) if n]
        return True, sorted(folders)
    except Exception as exc:  # noqa: BLE001
        return False, f"LIST failed: {exc}"
    finally:
        _quiet_logout(conn)


def count_messages(config: ImapConfig, folders: list[str]) -> tuple[bool, dict[str, int] | str]:
    """Message count per folder via STATUS. Never raises."""
    try:
        conn = _connect(config)
    except Exception as exc:  # noqa: BLE001
        return False, f"connect/login failed: {exc}"
    counts: dict[str, int] = {}
    try:
        for folder in folders:
            try:
                typ, data = conn.status(f'"{folder}"', "(MESSAGES)")
                if typ == "OK" and data and data[0]:
                    text = data[0].decode(errors="replace") if isinstance(data[0], bytes) else str(data[0])
                    counts[folder] = int(text.split("MESSAGES")[1].strip(" ()"))
                else:
                    counts[folder] = -1  # unknown; still listed, still consented
            except Exception:  # noqa: BLE001
                counts[folder] = -1  # unknown; still listed, still consented
        return True, counts
    finally:
        _quiet_logout(conn)


def _quiet_logout(conn: Any) -> None:
    try:
        conn.logout()
    except Exception:  # noqa: BLE001
        pass


def parse_message_metadata(raw: bytes) -> dict[str, Any]:
    """Parse headers + a plain-text body preview from an RFC822 message."""
    msg = message_from_bytes(raw, policy=email_policy)
    preview = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    preview = part.get_content()[:2000]
                except Exception:  # noqa: BLE001
                    preview = ""
                break
    elif msg.get_content_type() == "text/plain":
        try:
            preview = msg.get_content()[:2000]
        except Exception:  # noqa: BLE001
            preview = ""
    return {
        "subject": str(msg.get("Subject", "")),
        "from": str(msg.get("From", "")),
        "to": str(msg.get("To", "")),
        "date": str(msg.get("Date", "")),
        "message_id": str(msg.get("Message-ID", "")),
        "content_type": msg.get_content_type(),
        "body_preview": preview,
    }


def derive_email_staging_dir(
    case: str, account: str, ts: str, base: Path = FETCH_ROOT
) -> Path:
    return base / _sanitize_component(case) / f"email__{_sanitize_component(account)}__{ts}"


def stage_email_fetch(
    config: ImapConfig,
    folders: list[str],
    staging_dir: Path,
    consent_hash: str,
    case: str = "",
    cancel: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Fetch every message in ``folders`` into the staging dir.

    Per-folder/per-message errors are collected into the manifest, never
    raised. Cancellation between messages is clean: the manifest records
    exactly what was staged before the stop.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)
    messages: list[dict[str, Any]] = []
    errors: list[str] = []
    cancelled = False

    conn = _connect(config)  # raises -> caller ledgers fetch_error
    try:
        # Count first for progress reporting.
        total = 0
        per_folder_uids: dict[str, list[bytes]] = {}
        for folder in folders:
            try:
                typ, _ = conn.select(f'"{folder}"', readonly=True)
                if typ != "OK":
                    errors.append(f"{folder}: SELECT failed ({typ})")
                    continue
                typ, data = conn.search(None, "ALL")
                uids = data[0].split() if typ == "OK" and data and data[0] else []
                per_folder_uids[folder] = uids
                total += len(uids)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{folder}: {exc}")

        done = 0
        for folder, uids in per_folder_uids.items():
            if cancelled:
                break
            conn.select(f'"{folder}"', readonly=True)
            folder_dir = staging_dir / _sanitize_component(folder)
            folder_dir.mkdir(parents=True, exist_ok=True)
            for uid in uids:
                if cancel is not None and cancel.is_set():
                    cancelled = True
                    break
                uid_s = uid.decode(errors="replace")
                try:
                    typ, data = conn.fetch(uid, "(RFC822)")
                    raw = next(
                        (part[1] for part in data or []
                         if isinstance(part, tuple) and part[1]),
                        None,
                    )
                    if typ != "OK" or raw is None:
                        errors.append(f"{folder}/{uid_s}: FETCH failed ({typ})")
                        continue
                    eml_path = folder_dir / f"{uid_s}.eml"
                    eml_path.write_bytes(raw)
                    meta = parse_message_metadata(raw)
                    meta["uid"] = uid_s
                    meta["folder"] = folder
                    meta["sha256"] = _sha256_file(eml_path)
                    meta["size"] = len(raw)
                    (folder_dir / f"{uid_s}.json").write_text(
                        json.dumps(meta, indent=2, sort_keys=True),
                        encoding="utf-8",
                    )
                    messages.append({
                        "folder": folder,
                        "uid": uid_s,
                        "eml": str(eml_path),
                        "sha256": meta["sha256"],
                        "size": len(raw),
                        "subject": meta["subject"],
                        "from": meta["from"],
                        "date": meta["date"],
                    })
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{folder}/{uid_s}: {exc}")
                done += 1
                if progress is not None:
                    progress(done, total)
    finally:
        _quiet_logout(conn)

    manifest: dict[str, Any] = {
        "case": case,
        "consent_hash": consent_hash,
        "ts": _utc_now(),
        "account": config.account,
        "staging_dir": str(staging_dir),
        "folders": folders,
        "message_count": len(messages),
        "total_bytes": sum(m["size"] for m in messages),
        "cancelled": cancelled,
        "messages": messages,
        "errors": errors,
    }
    (staging_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest


# ---------------------------------------------------------------------------
# Tk tab (all widget access on the UI thread via the queue + after() pump)
# ---------------------------------------------------------------------------
class EmailIngestTab(ttk.Frame):
    """IMAP fetch + consent gate, as a notebook tab."""

    def __init__(self, parent: ttk.Notebook, status_setter: Callable[[str], None]) -> None:
        super().__init__(parent, padding=6)
        self._set_status = status_setter
        self._q: queue.Queue = queue.Queue()
        self._ledger = ConsentLedger(EMAIL_LEDGER_PATH)
        self._session_grants: list[dict[str, Any]] = []  # in-memory only
        self._busy = False
        self._cancel = threading.Event()
        self._build()
        self._refresh_ledger_label()
        self.after(100, self._poll)

    # -------------------------------------------------------------- UI build
    def _build(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill=tk.X)
        self.ledger_lbl = ttk.Label(header, text="Email consent ledger: …")
        self.ledger_lbl.pack(side=tk.LEFT)

        form = ttk.LabelFrame(self, text="IMAP account", padding=8)
        form.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(form, text="Host:").grid(row=0, column=0, sticky=tk.W)
        self.host_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.host_var, width=32).grid(row=0, column=1, sticky=tk.W, padx=6)
        ttk.Label(form, text="Port:").grid(row=0, column=2, sticky=tk.W)
        self.port_var = tk.StringVar(value="993")
        ttk.Entry(form, textvariable=self.port_var, width=7).grid(row=0, column=3, sticky=tk.W, padx=6)
        self.ssl_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="SSL/TLS", variable=self.ssl_var).grid(row=0, column=4, sticky=tk.W)

        ttk.Label(form, text="User:").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        self.user_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.user_var, width=32).grid(row=1, column=1, sticky=tk.W, padx=6, pady=(6, 0))
        ttk.Label(form, text="Password:").grid(row=1, column=2, sticky=tk.W, pady=(6, 0))
        self.pass_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.pass_var, width=20, show="•").grid(row=1, column=3, sticky=tk.W, padx=6, pady=(6, 0))
        ttk.Button(form, text="Connect && list folders", command=self._on_connect).grid(row=1, column=4, sticky=tk.W, padx=(12, 0), pady=(6, 0))

        mid = ttk.LabelFrame(self, text="Folders (multi-select)", padding=4)
        mid.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.folder_list = tk.Listbox(mid, selectmode=tk.EXTENDED, height=8, exportselection=False)
        fscroll = ttk.Scrollbar(mid, orient=tk.VERTICAL, command=self.folder_list.yview)
        self.folder_list.configure(yscrollcommand=fscroll.set)
        self.folder_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        fscroll.pack(side=tk.RIGHT, fill=tk.Y)

        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(bar, text="Destination case:").pack(side=tk.LEFT)
        self.case_var = tk.StringVar(value=f"EMAIL-{datetime.now(UTC):%Y%m%d}")
        ttk.Entry(bar, textvariable=self.case_var, width=28).pack(side=tk.LEFT, padx=6)
        ttk.Label(bar, text=f"Staging: {FETCH_ROOT}").pack(side=tk.LEFT, padx=(12, 0))
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=self._cancel.set, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.RIGHT, padx=(6, 0))
        self.fetch_btn = ttk.Button(bar, text="Fetch selected folders", command=self._on_fetch)
        self.fetch_btn.pack(side=tk.RIGHT)

    def _config(self) -> ImapConfig:
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            port = 993 if self.ssl_var.get() else 143
        return ImapConfig(
            host=self.host_var.get().strip(),
            port=port,
            user=self.user_var.get().strip(),
            password=self.pass_var.get(),
            use_ssl=self.ssl_var.get(),
        )

    # ------------------------------------------------------------ thread pump
    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._q.get_nowait()
                try:
                    self._dispatch(kind, payload)
                except Exception as exc:  # noqa: BLE001
                    self._busy = False
                    self._set_status(f"Email UI error: {exc}")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _dispatch(self, kind: str, payload: Any) -> None:
        if kind == "folders":
            self._on_folders(payload)
        elif kind == "probe_done":
            self._on_probe_done(*payload)
        elif kind == "progress":
            done, total = payload
            self._set_status(f"Fetching message {done}/{total}…")
        elif kind == "fetch_done":
            self._on_fetch_done(payload)
        elif kind == "fetch_error":
            self._on_fetch_error(payload)

    # --------------------------------------------------------------- connect
    def _on_connect(self) -> None:
        config = self._config()
        if not config.host or not config.user:
            self._set_status("Email: host and user are required")
            return
        self._set_status(f"Connecting to {config.host}…")

        def worker() -> None:
            self._q.put(("folders", list_folders(config)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_folders(self, result: tuple[bool, list[str] | str]) -> None:
        ok, data = result
        if not ok:
            self._set_status(f"IMAP error: {data}")
            return
        self.folder_list.delete(0, tk.END)
        for name in data:
            self.folder_list.insert(tk.END, name)
        self._set_status(f"{len(data)} folder(s) listed")

    # --------------------------------------------------------------- consent
    def _session_grant_for(self, account: str) -> dict[str, Any] | None:
        for grant in self._session_grants:
            if grant["account"] == account:
                return grant
        return None

    def _on_fetch(self) -> None:
        if self._busy:
            self._set_status("Email fetch already in progress")
            return
        folders = [self.folder_list.get(i) for i in self.folder_list.curselection()]
        if not folders:
            self._set_status("Email: select at least one folder")
            return
        config = self._config()
        case = self.case_var.get().strip() or f"EMAIL-{datetime.now(UTC):%Y%m%d}"
        self._busy = True
        self._cancel.clear()
        self._set_status(f"Counting messages in {len(folders)} folder(s)…")

        def worker() -> None:
            self._q.put(("probe_done", (config, folders, case, count_messages(config, folders))))

        threading.Thread(target=worker, daemon=True).start()

    def _on_probe_done(
        self,
        config: ImapConfig,
        folders: list[str],
        case: str,
        counts: tuple[bool, dict[str, int] | str],
    ) -> None:
        ok, data = counts
        if not ok:
            self._busy = False
            self._set_status(f"IMAP error: {data}")
            return
        total = sum(v for v in data.values() if v > 0)
        grant = self._session_grant_for(config.account)
        if grant is not None:
            self._ledger.append(
                "request",
                account=config.account, folders=folders, case=case,
                message_count=total, via_session=grant["hash"],
            )
            self._refresh_ledger_label()
            self._start_fetch(config, folders, case, grant["hash"])
            return

        self._ledger.append(
            "request", account=config.account, folders=folders,
            case=case, message_count=total,
        )
        decision = self._consent_dialog(config, folders, case, data, total)
        if decision == "refuse":
            self._ledger.append(
                "refuse", account=config.account, folders=folders,
                case=case, message_count=total,
            )
            self._busy = False
            self._set_status("Email fetch refused — logged to consent ledger")
            self._refresh_ledger_label()
            return
        if decision == "session":
            entry = self._ledger.append(
                "grant_session", account=config.account, folders=folders,
                case=case, message_count=total,
            )
            self._session_grants.append({"account": config.account, "hash": entry["hash"]})
            self._refresh_ledger_label()
            self._start_fetch(config, folders, case, entry["hash"])
            return
        entry = self._ledger.append(
            "grant_once", account=config.account, folders=folders,
            case=case, message_count=total,
        )
        self._refresh_ledger_label()
        self._start_fetch(config, folders, case, entry["hash"])

    def _consent_dialog(
        self,
        config: ImapConfig,
        folders: list[str],
        case: str,
        counts: dict[str, int],
        total: int,
    ) -> str:
        """Modal consent gate. Any exit other than a button is a refusal."""
        dlg = tk.Toplevel(self)
        dlg.title("Consent required — email fetch")
        dlg.transient(self.winfo_toplevel())
        dlg.grab_set()
        result = {"decision": "refuse"}

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            body,
            text=f"Email will be fetched from account: {config.account}",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(anchor=tk.W)
        listing = tk.Text(body, width=64, height=8, wrap=tk.NONE)
        listing.pack(fill=tk.BOTH, expand=True, pady=(4, 8))
        listing.insert(tk.END, "\n".join(f"{f}  ({counts.get(f, '?')} messages)" for f in folders))
        listing.config(state=tk.DISABLED)

        ttk.Label(body, text=f"Total messages: {total}").pack(anchor=tk.W)
        ttk.Label(body, text=f"Destination case: {case}").pack(anchor=tk.W)
        ttk.Label(
            body,
            text="Each message is stored as raw RFC822 (.eml) with a SHA-256 "
            "manifest, and this decision will be recorded in the "
            "tamper-evident consent ledger. The server is accessed read-only.",
            wraplength=520,
        ).pack(anchor=tk.W, pady=(8, 0))

        def choose(d: str) -> None:
            result["decision"] = d
            dlg.destroy()

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text="Accept once", command=lambda: choose("once")).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Accept for this session", command=lambda: choose("session")).pack(side=tk.LEFT, padx=8)
        ttk.Button(buttons, text="Refuse", command=lambda: choose("refuse")).pack(side=tk.RIGHT)

        dlg.protocol("WM_DELETE_WINDOW", lambda: choose("refuse"))
        dlg.bind("<Escape>", lambda _e: choose("refuse"))
        self.wait_window(dlg)
        return result["decision"]

    # ----------------------------------------------------------------- fetch
    def _start_fetch(
        self, config: ImapConfig, folders: list[str], case: str, consent_hash: str
    ) -> None:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        staging = derive_email_staging_dir(case, config.account, ts)
        self.cancel_btn.config(state=tk.NORMAL)

        def progress(done: int, total: int) -> None:
            self._q.put(("progress", (done, total)))

        def worker() -> None:
            try:
                manifest = stage_email_fetch(
                    config, folders, staging, consent_hash, case,
                    cancel=self._cancel, progress=progress,
                )
                self._q.put(("fetch_done", manifest))
            except Exception as exc:  # noqa: BLE001 — fail-closed: log, ship nothing silently
                self._q.put(("fetch_error", {"case": case, "error": str(exc),
                                             "consent_hash": consent_hash}))

        threading.Thread(target=worker, daemon=True).start()

    def _on_fetch_done(self, manifest: dict[str, Any]) -> None:
        manifest_path = Path(manifest["staging_dir"]) / "manifest.json"
        self._ledger.append(
            "fetch_complete",
            case=manifest["case"],
            consent_hash=manifest["consent_hash"],
            staging_dir=manifest["staging_dir"],
            message_count=manifest["message_count"],
            total_bytes=manifest["total_bytes"],
            cancelled=manifest["cancelled"],
            error_count=len(manifest["errors"]),
            manifest_sha256=_sha256_file(manifest_path),
        )
        self._busy = False
        self.cancel_btn.config(state=tk.DISABLED)
        self._refresh_ledger_label()
        note = " (cancelled)" if manifest["cancelled"] else ""
        self._set_status(
            f"Fetched {manifest['message_count']} message(s){note}, "
            f"{len(manifest['errors'])} error(s) → {manifest['staging_dir']}"
        )

    def _on_fetch_error(self, payload: dict[str, Any]) -> None:
        self._ledger.append(
            "fetch_error",
            case=payload["case"],
            consent_hash=payload["consent_hash"],
            error=payload["error"],
        )
        self._busy = False
        self.cancel_btn.config(state=tk.DISABLED)
        self._refresh_ledger_label()
        self._set_status(f"Email fetch failed (logged): {payload['error']}")

    # ---------------------------------------------------------------- ledger
    def _refresh_ledger_label(self) -> None:
        intact, count, broken_at = self._ledger.verify()
        if intact:
            self.ledger_lbl.config(
                text=f"Email consent ledger: intact ({count} entries)",
                foreground="#2a7d2a",
            )
        else:
            self.ledger_lbl.config(
                text=f"Email consent ledger: BROKEN CHAIN at entry {broken_at}",
                foreground="#c0392b",
            )
