#!/usr/bin/env python3
"""Consent-gated file fetch for the ANCHORUM desktop app.

The desktop app is what runs on the operator's machine and touches the disks,
so this is where the consent gate lives. The protocol:

1. Default-deny — no byte is read or copied without an affirmative Accept.
2. Informed scope — the dialog shows the exact paths, file count, total
   bytes, destination case, and states that SHA-256 manifests are computed.
3. Three choices — Accept once / Refuse / Accept for session. Session grants
   are scoped to the path prefixes shown, live only in memory, die with the
   app, and are themselves logged.
4. Tamper-evident ledger — every request, grant, refusal, and completed
   fetch is appended to a hash-chained JSONL ledger. A refusal is as much a
   recorded fact as a grant.
5. Chain of custody — every accepted fetch produces a manifest: source,
   staged path, size, SHA-256 per file, plus the ledger hash of the consent
   that authorized it.

Deliberately absent: any form of privilege escalation. Unreadable files
surface as ``(access denied)`` or manifest errors. Run the app with the
rights you need.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import threading
import tkinter as tk
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent
LEDGER_PATH = REPO_ROOT / "consent_ledger.jsonl"
FETCH_ROOT = REPO_ROOT / "fetched"

# Pseudo / virtual filesystems that are never real evidence sources.
PSEUDO_FSTYPES = frozenset({
    "autofs", "binfmt_misc", "bpf", "cgroup", "cgroup2", "configfs",
    "debugfs", "devpts", "devtmpfs", "efivarfs", "fusectl", "hugetlbfs",
    "mqueue", "nsfs", "overlay", "proc", "pstore", "ramfs", "securityfs",
    "shm", "squashfs", "sysfs", "tmpfs", "tracefs",
})


# ---------------------------------------------------------------------------
# Pure logic (headless-testable — no Tk below this line)
# ---------------------------------------------------------------------------
def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} PiB"


@dataclass
class Partition:
    device: str
    mountpoint: str
    fstype: str
    free_bytes: int | None
    total_bytes: int | None


def scan_partitions(mounts_text: str | None = None) -> list[Partition]:
    """Return real mounted filesystems from /proc/mounts, root first.

    Pseudo filesystems (proc/sysfs/tmpfs/squashfs/…) are filtered out.
    ``mounts_text`` is injectable for tests.
    """
    if mounts_text is None:
        mounts_text = Path("/proc/mounts").read_text(encoding="utf-8")
    seen: set[str] = set()
    parts: list[Partition] = []
    for line in mounts_text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        device, mountpoint, fstype = fields[0], fields[1], fields[2]
        mountpoint = mountpoint.replace("\\040", " ")  # octal-escaped spaces
        if (
            fstype in PSEUDO_FSTYPES
            or fstype.startswith(("fuse.", "cgroup"))
            or mountpoint in seen
        ):
            continue
        seen.add(mountpoint)
        try:
            st = os.statvfs(mountpoint)
            free: int | None = st.f_bavail * st.f_frsize
            total: int | None = st.f_blocks * st.f_frsize
        except OSError:
            free = total = None
        parts.append(Partition(device, mountpoint, fstype, free, total))
    parts.sort(key=lambda p: (p.mountpoint != "/", p.mountpoint))
    return parts


class ConsentLedger:
    """Append-only, hash-chained JSONL consent ledger.

    Each entry embeds the SHA-256 of the previous entry (``prev``) and its
    own ``hash`` over the canonical JSON of the entry without the ``hash``
    field. Editing any historical entry breaks the chain at exactly that
    entry.
    """

    def __init__(self, path: Path = LEDGER_PATH) -> None:
        self.path = Path(path)

    def _read_entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
        return entries

    @staticmethod
    def _hash(entry: dict[str, Any]) -> str:
        body = {k: v for k, v in entry.items() if k != "hash"}
        return hashlib.sha256(_canonical(body)).hexdigest()

    def append(self, event: str, **fields: Any) -> dict[str, Any]:
        entries = self._read_entries()
        entry: dict[str, Any] = {
            "seq": (entries[-1]["seq"] + 1) if entries else 1,
            "ts": _utc_now(),
            "event": event,
            **fields,
            "prev": entries[-1]["hash"] if entries else None,
        }
        entry["hash"] = self._hash(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def verify(self) -> tuple[bool, int, int | None]:
        """Return (intact, entry_count, first_broken_seq). Fail-closed."""
        try:
            entries = self._read_entries()
        except (json.JSONDecodeError, OSError):
            return False, 0, 1
        prev: str | None = None
        for idx, entry in enumerate(entries):
            if entry.get("prev") != prev or self._hash(entry) != entry.get("hash"):
                return False, len(entries), entry.get("seq", idx + 1)
            prev = entry["hash"]
        return True, len(entries), None


@dataclass
class Probe:
    file_count: int
    total_bytes: int
    errors: list[str] = field(default_factory=list)


def probe_paths(paths: list[Path]) -> Probe:
    """Count files and bytes under the selection. Read-only; errors captured."""
    count = 0
    total = 0
    errors: list[str] = []

    def _onerror(err: OSError) -> None:
        errors.append(f"{err.filename}: {err.strerror}")

    for p in paths:
        if p.is_dir():
            for root, _dirs, names in os.walk(p, onerror=_onerror):
                for name in names:
                    fp = Path(root) / name
                    try:
                        st = fp.stat()
                    except OSError as exc:
                        errors.append(f"{fp}: {exc.strerror}")
                        continue
                    count += 1
                    total += st.st_size
        else:
            try:
                st = p.stat()
            except OSError as exc:
                errors.append(f"{p}: {exc.strerror}")
                continue
            count += 1
            total += st.st_size
    return Probe(count, total, errors)


def _sanitize_component(text: str) -> str:
    return "".join(c if (c.isalnum() or c in "-._") else "_" for c in text)


def derive_source_token(source_root: Path) -> str:
    """`/mnt/blackstar/vol-hdd-a/x` -> `mnt__blackstar__vol-hdd-a__x`."""
    parts = [p for p in source_root.parts if p != "/"]
    return "__".join(_sanitize_component(p) for p in parts)


def derive_staging_dir(
    case: str, source_root: Path, ts: str, base: Path = FETCH_ROOT
) -> Path:
    return (
        base
        / _sanitize_component(case)
        / f"{derive_source_token(source_root)}__{ts}"
    )


def stage_fetch(
    paths: list[Path],
    staging_dir: Path,
    consent_hash: str,
    case: str = "",
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Copy the selection into the staging dir and write manifest.json.

    Per-file errors are collected, never raised; the manifest always records
    the consent ledger hash that authorized the fetch.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    errors: list[str] = []

    def _onerror(err: OSError) -> None:
        errors.append(f"{err.filename}: {err.strerror}")

    targets: list[tuple[Path, Path]] = []  # (source, relative-destination)
    for p in paths:
        if p.is_dir():
            for root, _dirs, names in os.walk(p, onerror=_onerror):
                for name in names:
                    src = Path(root) / name
                    targets.append((src, src.relative_to(p.parent)))
        else:
            targets.append((p, Path(p.name)))

    total = len(targets)
    for idx, (src, rel) in enumerate(targets, 1):
        dest = staging_dir / "payload" / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            files.append({
                "source": str(src),
                "destination": str(dest),
                "size": dest.stat().st_size,
                "sha256": _sha256_file(dest),
            })
        except OSError as exc:
            errors.append(f"{src}: {exc.strerror or exc}")
        if progress is not None:
            progress(idx, total)

    manifest: dict[str, Any] = {
        "case": case,
        "consent_hash": consent_hash,
        "ts": _utc_now(),
        "staging_dir": str(staging_dir),
        "file_count": len(files),
        "total_bytes": sum(f["size"] for f in files),
        "files": files,
        "errors": errors,
    }
    (staging_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest


# ---------------------------------------------------------------------------
# Tk tab (all widget access on the UI thread via the queue + after() pump)
# ---------------------------------------------------------------------------
class FetchTab(ttk.Frame):
    """Partition browser + consent-gated fetch, as a notebook tab."""

    def __init__(self, parent: ttk.Notebook, status_setter: Callable[[str], None]) -> None:
        super().__init__(parent, padding=6)
        self._set_status = status_setter
        self._q: queue.Queue = queue.Queue()
        self._ledger = ConsentLedger()
        self._session_grants: list[dict[str, Any]] = []  # in-memory only
        self._busy = False
        self._paths: dict[str, Path] = {}  # tree iid -> real path
        self._populated: set[str] = set()
        self._counter = 0
        self._build()
        self._refresh_ledger_label()
        self._load_partitions()
        self.after(100, self._poll)

    # -------------------------------------------------------------- UI build
    def _build(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill=tk.X)
        self.ledger_lbl = ttk.Label(header, text="Consent ledger: …")
        self.ledger_lbl.pack(side=tk.LEFT)
        ttk.Button(header, text="Rescan partitions", command=self._load_partitions).pack(
            side=tk.RIGHT
        )

        paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

        top = ttk.LabelFrame(paned, text="Mounted filesystems", padding=4)
        paned.add(top, weight=0)
        cols = ("device", "mountpoint", "fstype", "free", "total")
        self.part_tree = ttk.Treeview(top, columns=cols, show="headings", height=5)
        for c, w in zip(cols, (220, 260, 90, 100, 100)):
            self.part_tree.heading(c, text=c)
            self.part_tree.column(c, width=w, anchor=tk.W)
        self.part_tree.pack(fill=tk.X)

        mid = ttk.LabelFrame(
            paned,
            text="Files (double-click to expand — multi-select any mix of files and folders)",
            padding=4,
        )
        paned.add(mid, weight=1)
        self.tree = ttk.Treeview(mid, show="tree", selectmode="extended")
        scroll = ttk.Scrollbar(mid, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewOpen>>", self._on_open)

        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(bar, text="Destination case:").pack(side=tk.LEFT)
        self.case_var = tk.StringVar(
            value=f"FETCH-{datetime.now(UTC).strftime('%Y%m%d')}"
        )
        ttk.Entry(bar, textvariable=self.case_var, width=28).pack(side=tk.LEFT, padx=6)
        ttk.Label(bar, text=f"Staging: {FETCH_ROOT}").pack(side=tk.LEFT, padx=(12, 0))
        self.fetch_btn = ttk.Button(bar, text="Fetch selected", command=self._on_fetch)
        self.fetch_btn.pack(side=tk.RIGHT)

    # ------------------------------------------------------------ partitions
    def _load_partitions(self) -> None:
        self.part_tree.delete(*self.part_tree.get_children())
        self.tree.delete(*self.tree.get_children())
        self._paths.clear()
        self._populated.clear()
        try:
            parts = scan_partitions()
        except OSError as exc:
            self._set_status(f"Partition scan failed: {exc}")
            return
        for p in parts:
            self.part_tree.insert(
                "",
                tk.END,
                values=(
                    p.device,
                    p.mountpoint,
                    p.fstype,
                    _fmt_bytes(p.free_bytes),
                    _fmt_bytes(p.total_bytes),
                ),
            )
            iid = self._new_iid()
            self._paths[iid] = Path(p.mountpoint)
            self.tree.insert("", tk.END, iid=iid, text=p.mountpoint, open=False)
            self._insert_dummy(iid)

    # --------------------------------------------------------------- browser
    def _new_iid(self) -> str:
        self._counter += 1
        return f"n{self._counter}"

    def _insert_dummy(self, iid: str) -> None:
        self.tree.insert(iid, tk.END, iid=f"{iid}_dummy", text="…")

    def _on_open(self, _event: Any = None) -> None:
        iid = self.tree.focus()
        if not iid or iid in self._populated or iid not in self._paths:
            return
        self._populate(iid, self._paths[iid])

    def _populate(self, iid: str, path: Path) -> None:
        self.tree.delete(*self.tree.get_children(iid))
        self._populated.add(iid)
        try:
            entries = list(path.iterdir())
        except OSError:
            self.tree.insert(iid, tk.END, text="(access denied)")
            return
        dirs: list[Path] = []
        files: list[Path] = []
        for e in entries:
            try:
                (dirs if e.is_dir() else files).append(e)
            except OSError:
                files.append(e)
        for d in sorted(dirs, key=lambda x: x.name.lower()):
            child = self._new_iid()
            self._paths[child] = d
            self.tree.insert(iid, tk.END, iid=child, text=f"{d.name}/")
            self._insert_dummy(child)
        for f in sorted(files, key=lambda x: x.name.lower()):
            child = self._new_iid()
            self._paths[child] = f
            self.tree.insert(iid, tk.END, iid=child, text=f.name)

    # ------------------------------------------------------------ thread pump
    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._q.get_nowait()
                try:
                    self._dispatch(kind, payload)
                except Exception as exc:
                    self._busy = False
                    self._set_status(f"Fetch UI error: {exc}")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _dispatch(self, kind: str, payload: Any) -> None:
        if kind == "probe_done":
            self._on_probe_done(*payload)
        elif kind == "progress":
            done, total = payload
            self._set_status(f"Fetching {done}/{total}…")
        elif kind == "fetch_done":
            self._on_fetch_done(payload)
        elif kind == "fetch_error":
            self._on_fetch_error(payload)

    # -------------------------------------------------------------- consent
    @staticmethod
    def _dedupe(paths: list[Path]) -> list[Path]:
        """Drop selected paths already covered by a selected ancestor."""
        strs = [str(p) for p in paths]
        return [
            p
            for p, s in zip(paths, strs)
            if not any(s != o and s.startswith(o + os.sep) for o in strs)
        ]

    def _session_grant_for(self, paths: list[Path]) -> dict[str, Any] | None:
        for grant in self._session_grants:
            prefixes = grant["prefixes"]
            if all(
                any(str(p) == pre or str(p).startswith(pre + os.sep) for pre in prefixes)
                for p in paths
            ):
                return grant
        return None

    def _on_fetch(self) -> None:
        if self._busy:
            self._set_status("Fetch already in progress")
            return
        paths = self._dedupe(
            [self._paths[iid] for iid in self.tree.selection() if iid in self._paths]
        )
        if not paths:
            self._set_status("Fetch: nothing selected")
            return
        case = self.case_var.get().strip() or f"FETCH-{datetime.now(UTC):%Y%m%d}"
        self._busy = True
        self._set_status(f"Scanning {len(paths)} path(s)…")
        threading.Thread(target=self._probe_worker, args=(paths, case), daemon=True).start()

    def _probe_worker(self, paths: list[Path], case: str) -> None:
        probe = probe_paths(paths)
        self._q.put(("probe_done", (paths, case, probe)))

    def _on_probe_done(self, paths: list[Path], case: str, probe: Probe) -> None:
        grant = self._session_grant_for(paths)
        if grant is not None:
            entry = self._ledger.append(
                "request",
                paths=[str(p) for p in paths],
                case=case,
                file_count=probe.file_count,
                total_bytes=probe.total_bytes,
                via_session=grant["hash"],
            )
            self._refresh_ledger_label()
            self._start_fetch(paths, case, grant["hash"])
            return

        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        common = Path(os.path.commonpath([str(p) for p in paths]))
        staging = derive_staging_dir(case, common, ts)
        self._ledger.append(
            "request",
            paths=[str(p) for p in paths],
            case=case,
            file_count=probe.file_count,
            total_bytes=probe.total_bytes,
        )
        decision = self._consent_dialog(paths, case, probe, staging)
        if decision == "refuse":
            self._ledger.append(
                "refuse", paths=[str(p) for p in paths], case=case,
                file_count=probe.file_count, total_bytes=probe.total_bytes,
            )
            self._busy = False
            self._set_status("Fetch refused — logged to consent ledger")
            self._refresh_ledger_label()
            return
        if decision == "session":
            entry = self._ledger.append(
                "grant_session", paths=[str(p) for p in paths], case=case,
                file_count=probe.file_count, total_bytes=probe.total_bytes,
            )
            self._session_grants.append(
                {"prefixes": [str(p) for p in paths], "hash": entry["hash"]}
            )
            self._refresh_ledger_label()
            self._start_fetch(paths, case, entry["hash"], staging)
            return
        entry = self._ledger.append(
            "grant_once", paths=[str(p) for p in paths], case=case,
            file_count=probe.file_count, total_bytes=probe.total_bytes,
        )
        self._refresh_ledger_label()
        self._start_fetch(paths, case, entry["hash"], staging)

    def _consent_dialog(
        self, paths: list[Path], case: str, probe: Probe, staging: Path
    ) -> str:
        """Modal consent gate. Any exit other than a button is a refusal."""
        dlg = tk.Toplevel(self)
        dlg.title("Consent required — file fetch")
        dlg.transient(self.winfo_toplevel())
        dlg.grab_set()
        dlg.resizable(True, True)
        result = {"decision": "refuse"}

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            body, text="The following paths will be READ and COPIED:",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(anchor=tk.W)
        listing = tk.Text(body, width=84, height=10, wrap=tk.NONE)
        listing.pack(fill=tk.BOTH, expand=True, pady=(4, 8))
        listing.insert(tk.END, "\n".join(str(p) for p in paths))
        listing.config(state=tk.DISABLED)

        ttk.Label(body, text=f"Files: {probe.file_count}").pack(anchor=tk.W)
        ttk.Label(body, text=f"Total size: {_fmt_bytes(probe.total_bytes)}").pack(anchor=tk.W)
        ttk.Label(body, text=f"Destination case: {case}").pack(anchor=tk.W)
        ttk.Label(body, text=f"Staging folder: {staging}").pack(anchor=tk.W)
        ttk.Label(
            body,
            text="A SHA-256 manifest will be computed for every copied file, "
            "and this decision will be recorded in the tamper-evident consent ledger.",
            wraplength=560,
        ).pack(anchor=tk.W, pady=(8, 0))
        if probe.errors:
            ttk.Label(
                body,
                text=f"Scan warnings ({len(probe.errors)}): {probe.errors[0]}",
                foreground="#b26a00",
                wraplength=560,
            ).pack(anchor=tk.W, pady=(4, 0))

        def choose(d: str) -> None:
            result["decision"] = d
            dlg.destroy()

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text="Accept once", command=lambda: choose("once")).pack(
            side=tk.LEFT
        )
        ttk.Button(
            buttons, text="Accept for this session", command=lambda: choose("session")
        ).pack(side=tk.LEFT, padx=8)
        ttk.Button(buttons, text="Refuse", command=lambda: choose("refuse")).pack(
            side=tk.RIGHT
        )

        dlg.protocol("WM_DELETE_WINDOW", lambda: choose("refuse"))
        dlg.bind("<Escape>", lambda _e: choose("refuse"))
        self.wait_window(dlg)
        return result["decision"]

    # ----------------------------------------------------------------- fetch
    def _start_fetch(
        self,
        paths: list[Path],
        case: str,
        consent_hash: str,
        staging: Path | None = None,
    ) -> None:
        if staging is None:
            ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            common = Path(os.path.commonpath([str(p) for p in paths]))
            staging = derive_staging_dir(case, common, ts)

        def progress(done: int, total: int) -> None:
            self._q.put(("progress", (done, total)))

        def worker() -> None:
            try:
                manifest = stage_fetch(paths, staging, consent_hash, case, progress)
                self._q.put(("fetch_done", manifest))
            except Exception as exc:  # fail-closed: log, ship nothing silently
                self._q.put(("fetch_error", {"case": case, "error": str(exc),
                                             "consent_hash": consent_hash}))

        threading.Thread(target=worker, daemon=True).start()

    def _on_fetch_done(self, manifest: dict[str, Any]) -> None:
        manifest_path = Path(manifest["staging_dir"]) / "manifest.json"
        manifest_sha = _sha256_file(manifest_path)
        self._ledger.append(
            "fetch_complete",
            case=manifest["case"],
            consent_hash=manifest["consent_hash"],
            staging_dir=manifest["staging_dir"],
            file_count=manifest["file_count"],
            total_bytes=manifest["total_bytes"],
            error_count=len(manifest["errors"]),
            manifest_sha256=manifest_sha,
        )
        self._busy = False
        self._refresh_ledger_label()
        self._set_status(
            f"Fetched {manifest['file_count']} file(s), "
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
        self._refresh_ledger_label()
        self._set_status(f"Fetch failed (logged): {payload['error']}")

    # ---------------------------------------------------------------- ledger
    def _refresh_ledger_label(self) -> None:
        intact, count, broken_at = self._ledger.verify()
        if intact:
            self.ledger_lbl.config(
                text=f"Consent ledger: intact ({count} entries)",
                foreground="#2a7d2a",
            )
        else:
            self.ledger_lbl.config(
                text=f"Consent ledger: BROKEN CHAIN at entry {broken_at}",
                foreground="#c0392b",
            )
