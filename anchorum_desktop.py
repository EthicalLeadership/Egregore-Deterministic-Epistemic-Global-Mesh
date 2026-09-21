#!/usr/bin/env python3
"""ANCHORUM Desktop — native Tkinter client for the Egregore ANCHORUM site.

Full toolset, no browser. Talks directly to the local API server (default
https://127.0.0.1:8443, override via EGREGORE_BASE_URL).
Run:  .venv/bin/python anchorum_desktop.py
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
import tkinter as tk
from html import unescape
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

import requests
import urllib3

from ui_text import (
    append_text,
    install_context_menu,
    make_readonly_copyable,
    set_text,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = os.environ.get("EGREGORE_BASE_URL", "https://127.0.0.1:8443")
API_KEY = (Path(__file__).parent / "secrets" / "api_key.hex").read_text().strip()
HEADERS = {"X-API-Key": API_KEY, "Accept": "application/json"}
TIMEOUT = 180  # local LLM can be slow

# Single session for all requests — disables TLS verification for self-signed certs.
session = requests.Session()
session.verify = False
session.headers.update(HEADERS)

# Jobs API — adjust if the backend routes differ.
# Expected surface:
#   GET    {JOBS_ENDPOINT}                 -> list of jobs (or {"jobs": [...]})
#   GET    {JOBS_ENDPOINT}/{job_id}        -> job detail
#   DELETE {JOBS_ENDPOINT}/{job_id}        -> delete/cancel job
# Creation reuses the existing /batch endpoints (async batch already creates a
# server-side job), so job creation works even if the jobs routes are absent.
JOBS_ENDPOINT = "/api/v1/anchorum/jobs"
JOB_AUTO_REFRESH_S = 5

SEVERITIES = ("critical", "high", "medium", "low", "info")

logger = logging.getLogger("anchorum_desktop")

# Sentinel for the chat focus-case picker: discuss without any case context.
NO_CASE = "(no case)"


class AnchorumApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ANCHORUM — Legal Dossier AI")
        self.geometry("1150x720")
        self.minsize(900, 560)

        self._q: queue.Queue = queue.Queue()
        self._cases: list[str] = []
        self._active_case: str | None = None
        self._jobs: list[dict] = []
        self._active_job: str | None = None
        self._jobs_unavailable = False
        self._job_rows: dict[str, dict] = {}  # treeview iid -> job payload
        self._refilling_jobs = False
        # Chat focus case — independent of the Cases tab selection, so any
        # case can be discussed without switching tabs.
        self._chat_case: str | None = None
        self._chat_model: str = "?"
        self._tools: list[dict] = []
        self._build_ui()
        self.after(100, self._poll_queue)
        self._bg(self._load_cases)
        self._bg(self._load_dossiers)
        self._bg(self._load_status)
        self._bg(self._load_jobs)
        self._bg(self._load_models)
        self._chat_welcome()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=6)
        top.pack(fill=tk.X)
        ttk.Label(top, text="ANCHORUM", font=("TkDefaultFont", 14, "bold")).pack(side=tk.LEFT)
        ttk.Label(top, text="Legal Dossier — powered by Egregore").pack(side=tk.LEFT, padx=10)
        self.health_lbl = ttk.Label(top, text="●", foreground="#888888")
        self.health_lbl.pack(side=tk.RIGHT)
        ttk.Button(top, text="Refresh All", command=self._refresh_all).pack(side=tk.RIGHT, padx=(0, 8))

        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        self._build_cases_tab(nb)
        self._build_dossiers_tab(nb)
        self._build_chat_tab(nb)
        self._build_batch_tab(nb)
        self._build_fetch_tab(nb)
        self._build_email_tab(nb)
        self._build_jobs_tab(nb)
        self._build_system_tab(nb)
        self._build_factory_tab(nb)

        self.status = ttk.Label(self, text="Ready", anchor=tk.W, padding=(6, 2))
        self.status.pack(fill=tk.X, side=tk.BOTTOM)

    def _build_cases_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="Cases")

        paned = ttk.PanedWindow(tab, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned, width=240)
        paned.add(left, weight=0)
        ttk.Label(left, text="Cases").pack(anchor=tk.W)
        self.case_list = tk.Listbox(left, exportselection=False)
        self.case_list.pack(fill=tk.BOTH, expand=True)
        self.case_list.bind("<<ListboxSelect>>", self._on_case_select)
        case_btns = ttk.Frame(left)
        case_btns.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(case_btns, text="Refresh", command=lambda: self._bg(self._load_cases)).pack(side=tk.LEFT)
        ttk.Button(case_btns, text="New case…", command=self._create_case).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(case_btns, text="Delete case…", command=self._delete_case).pack(side=tk.LEFT, padx=(6, 0))

        right = ttk.Frame(paned)
        paned.add(right, weight=1)

        self.case_view_nb = ttk.Notebook(right)
        self.case_view_nb.pack(fill=tk.BOTH, expand=True)

        self.summary_txt = self._make_ro_text(self.case_view_nb, "Summary")
        self.anom_txt = self._make_ro_text(self.case_view_nb, "Anomalies")
        self.timeline_txt = self._make_ro_text(self.case_view_nb, "Timeline")
        self.report_txt = self._make_ro_text(self.case_view_nb, "Full Report")

        btn_row = ttk.Frame(right)
        btn_row.pack(fill=tk.X, pady=(4, 0))
        self.case_btns: list[ttk.Button] = []
        for label, cmd in (
            ("Load Summary", self._load_selected_summary),
            ("Load Anomalies", self._load_selected_anomalies),
            ("Load Timeline", self._load_selected_timeline),
            ("Load Full Report", self._load_selected_report),
            ("Export Report…", self._export_report),
        ):
            b = ttk.Button(btn_row, text=label, command=cmd)
            b.pack(side=tk.LEFT, padx=(0, 6))
            self.case_btns.append(b)

    def _build_dossiers_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="Dossiers")

        paned = ttk.PanedWindow(tab, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned, width=300)
        paned.add(left, weight=0)
        ttk.Label(left, text="Legal Dossiers").pack(anchor=tk.W)

        cols = ("case_id", "chunks", "sources")
        self.dossier_tree = ttk.Treeview(
            left, columns=cols, show="headings", height=12
        )
        self.dossier_tree.heading("case_id", text="Case ID")
        self.dossier_tree.heading("chunks", text="Chunks")
        self.dossier_tree.heading("sources", text="Sources")
        self.dossier_tree.column("case_id", width=160, anchor=tk.W)
        self.dossier_tree.column("chunks", width=60, anchor=tk.CENTER)
        self.dossier_tree.column("sources", width=60, anchor=tk.CENTER)
        self.dossier_tree.pack(fill=tk.BOTH, expand=True)
        self.dossier_tree.bind("<<TreeviewSelect>>", self._on_dossier_select)

        btn_row = ttk.Frame(left)
        btn_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(btn_row, text="Refresh", command=lambda: self._bg(self._load_dossiers)).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Attach dir…", command=self._attach_dossier_dir).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btn_row, text="Reindex", command=self._reindex_dossier).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btn_row, text="Chat", command=self._open_dossier_chat).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btn_row, text="Delete", command=self._delete_dossier).pack(side=tk.LEFT, padx=(6, 0))

        right = ttk.Frame(paned)
        paned.add(right, weight=1)

        self.dossier_detail = make_readonly_copyable(
            tk.Text(right, wrap=tk.WORD, height=12)
        )
        install_context_menu(self.dossier_detail)
        self.dossier_detail.pack(fill=tk.BOTH, expand=True)

        tools_frame = ttk.LabelFrame(right, text="Tools", padding=6)
        tools_frame.pack(fill=tk.X, pady=(8, 0))
        self.tools_list = tk.Listbox(tools_frame, height=4)
        self.tools_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Button(tools_frame, text="Launch", command=self._launch_tool).pack(
            side=tk.LEFT, padx=(6, 0)
        )

        self._load_tools_ui()

    def _build_chat_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="AI Agent")

        self.chat_log = make_readonly_copyable(tk.Text(tab, wrap=tk.WORD))
        install_context_menu(self.chat_log)
        self.chat_log.pack(fill=tk.BOTH, expand=True)
        self.chat_log.tag_config("you", foreground="#1a6ed1")
        self.chat_log.tag_config("agent", foreground="#177a3a")
        self.chat_log.tag_config("error", foreground="#c01c1c")
        self.chat_log.tag_config("meta", foreground="#888888")

        focus = ttk.Frame(tab)
        focus.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(focus, text="Focus case:").pack(side=tk.LEFT)
        self.chat_case_var = tk.StringVar(value=NO_CASE)
        self.chat_case_combo = ttk.Combobox(
            focus,
            textvariable=self.chat_case_var,
            state="readonly",
            width=30,
            values=[NO_CASE],
        )
        self.chat_case_combo.pack(side=tk.LEFT, padx=6)
        self.chat_case_combo.bind("<<ComboboxSelected>>", self._on_chat_case_change)
        self.model_lbl = ttk.Label(focus, text="Model: ?", foreground="#888888")
        self.model_lbl.pack(side=tk.LEFT, padx=(12, 0))
        self.knowledge_lbl = ttk.Label(
            focus, text="Knowledge: ?", foreground="#888888"
        )
        self.knowledge_lbl.pack(side=tk.LEFT, padx=(12, 0))
        ttk.Button(
            focus, text="Reindex case", command=lambda: self._bg(self._reindex_case)
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(
            focus,
            text="\u2014 report re-read from disk on every message",
            foreground="#888888",
        ).pack(side=tk.LEFT, padx=(12, 0))

        controls = ttk.Frame(tab)
        controls.pack(fill=tk.X, pady=(6, 0))
        # Multi-line input so file content can be pasted for AI exploration.
        # Enter sends; Shift+Enter inserts a newline.
        entry_frame = ttk.Frame(controls)
        entry_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.chat_entry = tk.Text(entry_frame, height=4, wrap=tk.WORD)
        entry_scroll = ttk.Scrollbar(
            entry_frame, orient=tk.VERTICAL, command=self.chat_entry.yview
        )
        self.chat_entry.configure(yscrollcommand=entry_scroll.set)
        self.chat_entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        entry_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        install_context_menu(self.chat_entry)
        self.chat_entry.bind("<Return>", self._on_chat_return)
        ttk.Button(controls, text="Ask Legal Dossier", command=lambda: self._send("legal")).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(controls, text="Ask Egregore", command=lambda: self._send("ask")).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(controls, text="Clear", command=self._clear_chat).pack(side=tk.LEFT, padx=(6, 0))

    def _build_batch_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=10)
        nb.add(tab, text="Batch / Fusion")

        form = ttk.LabelFrame(tab, text="Trigger ANCHORUM batch run", padding=10)
        form.pack(fill=tk.X)

        ttk.Label(form, text="Input directory:").grid(row=0, column=0, sticky=tk.W)
        self.batch_input = ttk.Entry(form, width=60)
        install_context_menu(self.batch_input)
        self.batch_input.grid(row=0, column=1, sticky=tk.EW, padx=6)
        ttk.Button(form, text="Browse…", command=self._browse_input).grid(row=0, column=2)

        ttk.Label(form, text="Case ID:").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        self.batch_case = ttk.Entry(form, width=40)
        install_context_menu(self.batch_case)
        self.batch_case.grid(row=1, column=1, sticky=tk.W, padx=6, pady=(6, 0))

        self.batch_fuse = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="Run RFE fusion after batch", variable=self.batch_fuse).grid(
            row=2, column=1, sticky=tk.W, padx=6, pady=(6, 0))

        btns = ttk.Frame(form)
        btns.grid(row=3, column=1, sticky=tk.W, padx=6, pady=(10, 0))
        ttk.Button(btns, text="Run (background)", command=lambda: self._run_batch(async_=True)).pack(side=tk.LEFT)
        ttk.Button(btns, text="Run (sync, small dirs)", command=lambda: self._run_batch(async_=False)).pack(side=tk.LEFT, padx=(6, 0))
        form.columnconfigure(1, weight=1)

        self.batch_out = self._make_ro_text(tab, None, height=12)
        self.batch_out.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

    def _build_jobs_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=8)
        nb.add(tab, text="Jobs")

        # ---- Create form --------------------------------------------------
        form = ttk.LabelFrame(tab, text="New job", padding=8)
        form.pack(fill=tk.X)

        ttk.Label(form, text="Input directory:").grid(row=0, column=0, sticky=tk.W)
        self.job_input = ttk.Entry(form, width=60)
        install_context_menu(self.job_input)
        self.job_input.grid(row=0, column=1, sticky=tk.EW, padx=6)
        ttk.Button(form, text="Browse…", command=self._browse_job_input).grid(row=0, column=2)

        ttk.Label(form, text="Case ID:").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        self.job_case = ttk.Entry(form, width=40)
        install_context_menu(self.job_case)
        self.job_case.grid(row=1, column=1, sticky=tk.W, padx=6, pady=(6, 0))

        self.job_fuse = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="Run RFE fusion after batch", variable=self.job_fuse).grid(
            row=2, column=1, sticky=tk.W, padx=6, pady=(6, 0))

        ttk.Button(form, text="Create job", command=self._create_job).grid(
            row=3, column=1, sticky=tk.W, padx=6, pady=(8, 0))
        form.columnconfigure(1, weight=1)

        # ---- Jobs table ----------------------------------------------------
        table_frame = ttk.LabelFrame(tab, text="Jobs", padding=4)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        cols = ("job_id", "case_id", "status", "created", "progress")
        self.job_tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=8)
        widths = {"job_id": 260, "case_id": 200, "status": 110, "created": 170, "progress": 80}
        headings = {"job_id": "Job ID", "case_id": "Case ID", "status": "Status",
                    "created": "Created", "progress": "Progress"}
        for c in cols:
            self.job_tree.heading(c, text=headings[c])
            self.job_tree.column(c, width=widths[c], anchor=tk.W)
        scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.job_tree.yview)
        self.job_tree.configure(yscrollcommand=scroll.set)
        self.job_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.job_tree.bind("<<TreeviewSelect>>", self._on_job_select)

        # Colour-code statuses
        self.job_tree.tag_configure("queued", foreground="#8a6d00")
        self.job_tree.tag_configure("running", foreground="#1a6ed1")
        self.job_tree.tag_configure("done", foreground="#177a3a")
        self.job_tree.tag_configure("completed", foreground="#177a3a")
        self.job_tree.tag_configure("failed", foreground="#c01c1c")
        self.job_tree.tag_configure("error", foreground="#c01c1c")
        self.job_tree.tag_configure("cancelled", foreground="#888888")
        self.job_tree.tag_configure("canceled", foreground="#888888")

        ctl = ttk.Frame(tab)
        ctl.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(ctl, text="Refresh", command=lambda: self._bg(self._load_jobs)).pack(side=tk.LEFT)
        self.job_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(ctl, text=f"Auto-refresh every {JOB_AUTO_REFRESH_S}s",
                        variable=self.job_auto).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(ctl, text="Delete selected job…", command=self._delete_selected_job).pack(side=tk.RIGHT)

        # ---- Detail pane ---------------------------------------------------
        detail_frame = ttk.LabelFrame(tab, text="Selected job detail", padding=4)
        detail_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.job_detail = make_readonly_copyable(
            tk.Text(detail_frame, wrap=tk.WORD, height=7)
        )
        install_context_menu(self.job_detail)
        dscroll = ttk.Scrollbar(detail_frame, orient=tk.VERTICAL, command=self.job_detail.yview)
        self.job_detail.configure(yscrollcommand=dscroll.set)
        self.job_detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        dscroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.after(JOB_AUTO_REFRESH_S * 1000, self._jobs_auto_tick)

    def _build_system_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="System")

        ctl = ttk.LabelFrame(tab, text="Governance controls", padding=8)
        ctl.pack(fill=tk.X)
        ttk.Button(ctl, text="Refresh Status", command=lambda: self._bg(self._load_status)).pack(side=tk.LEFT)
        ttk.Button(ctl, text="Freeze", command=lambda: self._freeze(True)).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(ctl, text="Unfreeze", command=lambda: self._freeze(False)).pack(side=tk.LEFT, padx=(6, 0))

        views = ttk.Frame(tab)
        views.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        cols = ttk.PanedWindow(views, orient=tk.HORIZONTAL)
        cols.pack(fill=tk.BOTH, expand=True)

        f1 = ttk.LabelFrame(cols, text="Status / Health", padding=4)
        cols.add(f1, weight=1)
        self.sys_status = self._make_ro_text_frame(f1)

        f2 = ttk.LabelFrame(cols, text="Key Health", padding=4)
        cols.add(f2, weight=1)
        self.sys_keys = self._make_ro_text_frame(f2)

        f2b = ttk.LabelFrame(cols, text="CI Health", padding=4)
        cols.add(f2b, weight=1)
        self.sys_ci = self._make_ro_text_frame(f2b)

        f3 = ttk.LabelFrame(cols, text="Freeze Audit Log", padding=4)
        cols.add(f3, weight=1)
        self.sys_audit = self._make_ro_text_frame(f3)

        f4 = ttk.LabelFrame(cols, text="AI Models", padding=4)
        cols.add(f4, weight=1)
        self.sys_models = self._make_ro_text_frame(f4)

        btns = ttk.Frame(tab)
        btns.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btns, text="Load Key Health", command=lambda: self._bg(self._load_key_health)).pack(side=tk.LEFT)
        ttk.Button(btns, text="Load CI Health", command=lambda: self._bg(self._load_ci_health)).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns, text="Load Audit Log", command=lambda: self._bg(self._load_audit)).pack(side=tk.LEFT, padx=(6, 0))

    # ----------------------------------------------------------- UI helpers
    def _make_ro_text(self, parent, tab_label: str | None, height: int = 10) -> tk.Text:
        frame = ttk.Frame(parent, padding=4)
        txt = make_readonly_copyable(tk.Text(frame, wrap=tk.WORD, height=height))
        install_context_menu(txt)
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=txt.yview)
        txt.configure(yscrollcommand=scroll.set)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        if tab_label is not None:
            parent.add(frame, text=tab_label)
        else:
            frame.pack(fill=tk.BOTH, expand=True)
        return txt

    def _make_ro_text_frame(self, frame: ttk.LabelFrame) -> tk.Text:
        txt = make_readonly_copyable(tk.Text(frame, wrap=tk.WORD))
        install_context_menu(txt)
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=txt.yview)
        txt.configure(yscrollcommand=scroll.set)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return txt

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        set_text(widget, text)

    def _build_fetch_tab(self, nb: ttk.Notebook) -> None:
        from file_fetch import FetchTab

        tab = FetchTab(nb, self._set_status)
        nb.add(tab, text="Fetch")

    def _build_email_tab(self, nb: ttk.Notebook) -> None:
        from email_ingest import EmailIngestTab

        tab = EmailIngestTab(nb, self._set_status)
        nb.add(tab, text="Email")

    def _build_factory_tab(self, nb: ttk.Notebook) -> None:
        from factory_tab import FactoryTab

        tab = FactoryTab(nb, self._set_status)
        nb.add(tab, text="Factory")

    # ------------------------------------------------------------ plumbing
    def _bg(self, fn, *args) -> None:
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _poll_queue(self) -> None:
        try:
            while True:
                fn, args = self._q.get_nowait()
                try:
                    fn(*args)
                except Exception as exc:
                    # A failing UI callback must not kill the poll loop.
                    self._set_status(f"UI error: {exc}")
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _ui(self, fn, *args) -> None:
        self._q.put((fn, args))

    def _set_status(self, text: str) -> None:
        if hasattr(self, "status"):
            self.status.config(text=text)

    def _append_chat(self, tag: str, who: str, text: str) -> None:
        append_text(self.chat_log, f"{who}\n", "meta")
        append_text(self.chat_log, f"{text}\n\n", tag)

    def _on_chat_return(self, event: tk.Event) -> str | None:
        if event.state & 0x0001:  # Shift held -> plain newline
            return None
        self._send("legal")
        return "break"  # swallow the newline

    def _clear_chat(self) -> None:
        self._set_text(self.chat_log, "")

    def _fill_cases(self, cases: list[str]) -> None:
        self._cases = cases
        self.chat_case_combo["values"] = [NO_CASE, *cases]
        self.case_list.delete(0, tk.END)
        for c in cases:
            self.case_list.insert(tk.END, c)
        if cases and self._active_case not in cases:
            # Auto-select the first case so every panel has real content.
            self.case_list.selection_set(0)
            self.case_list.see(0)
            self._on_case_select(None)

    def _selected_case(self) -> str | None:
        sel = self.case_list.curselection()
        if sel:
            self._active_case = self._cases[sel[0]]
        return self._active_case

    def _get(self, path: str, timeout: int = 30):
        r = session.get(f"{BASE_URL}{path}", timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict, timeout: int = TIMEOUT):
        r = session.post(
            f"{BASE_URL}{path}",
            json=payload, timeout=timeout,
        )
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str, timeout: int = 30):
        r = session.delete(f"{BASE_URL}{path}", timeout=timeout)
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            return {"status": r.status_code}

    def _refresh_all(self) -> None:
        self._bg(self._load_cases)
        self._bg(self._load_status)
        self._bg(self._load_key_health)
        self._bg(self._load_ci_health)
        self._bg(self._load_audit)
        self._bg(self._load_jobs)
        self._bg(self._load_models)
        self._bg(self._load_knowledge)
        self._bg(self._load_dossiers)

    # ------------------------------------------------------- cases actions
    # ------------------------------------------------------- case CRUD
    def _create_case(self) -> None:
        case_id = simpledialog.askstring(
            "New case",
            "Case ID (A–Z, 0–9, _ - : .):",
            parent=self,
        )
        if not case_id or not case_id.strip():
            return
        self._bg(self._create_case_bg, case_id.strip())

    def _create_case_bg(self, case_id: str) -> None:
        try:
            self._post_case_create(case_id)
            self._ui(self._set_status, f"Case {case_id} created")
            self._bg(self._load_cases)
        except requests.HTTPError as exc:
            body = exc.response.text[:400] if exc.response is not None else str(exc)
            code = exc.response.status_code if exc.response is not None else "?"
            self._ui(self._set_status, f"Create case failed (HTTP {code}): {body}")
        except Exception as exc:
            self._ui(self._set_status, f"Create case failed: {exc}")

    def _post_case_create(self, case_id: str) -> dict:
        r = session.post(
            f"{BASE_URL}/api/v1/anchorum/cases",
            json={"case_id": case_id, "operator": "desktop_app"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def _delete_case(self) -> None:
        case_id = self._selected_case()
        if not case_id:
            self._set_status("Select a case to delete")
            return
        if not messagebox.askyesno(
            "Delete case",
            f"Delete case {case_id}?\n\nThis removes its report, summary, and work "
            "directory from the writable workspace. Provenance .zarc chains are "
            "append-only evidence and are kept.\n\nThis cannot be undone.",
        ):
            return
        self._bg(self._delete_case_bg, case_id)

    def _delete_case_bg(self, case_id: str) -> None:
        try:
            self._delete(f"/api/v1/anchorum/cases/{case_id}", timeout=30)
            if self._active_case == case_id:
                self._active_case = None
                for widget in (self.summary_txt, self.anom_txt, self.timeline_txt, self.report_txt):
                    self._ui(self._set_text, widget, "")
            self._ui(self._set_status, f"Case {case_id} deleted")
        except requests.HTTPError as exc:
            body = exc.response.text[:400] if exc.response is not None else str(exc)
            code = exc.response.status_code if exc.response is not None else "?"
            self._ui(self._set_status, f"Delete case failed (HTTP {code}): {body}")
        except Exception as exc:
            self._ui(self._set_status, f"Delete case failed: {exc}")
        self._load_cases()

    # ---------------------------------------------------- dossier actions
    def _load_dossiers(self) -> None:
        self._ui(self._set_status, "Loading dossiers…")
        try:
            cases = self._get("/api/v1/anchorum/cases", timeout=15)
            self._ui(self._fill_dossiers, cases)
            self._ui(self._set_status, f"{len(cases)} dossier(s) loaded")
        except Exception as exc:
            self._ui(self._set_status, f"Failed to load dossiers: {exc}")

    def _fill_dossiers(self, cases: list[str]) -> None:
        self.dossier_tree.delete(*self.dossier_tree.get_children())
        for case_id in cases:
            iid = self.dossier_tree.insert(
                "", tk.END, iid=case_id, values=(case_id, "—", "—")
            )
            self._bg(self._fetch_dossier_row, iid, case_id)
        if cases and self._active_case not in cases:
            first = cases[0]
            self.dossier_tree.selection_set(first)
            self.dossier_tree.see(first)
            self._on_dossier_select(None)

    def _fetch_dossier_row(self, iid: str, case_id: str) -> None:
        try:
            stats = self._get(f"/api/v1/anchorum/cases/{case_id}/index", timeout=15)
            chunks = str(stats.get("chunks", 0)) if stats.get("indexed") else "—"
            sources_data = self._get(
                f"/api/v1/anchorum/cases/{case_id}/sources", timeout=15
            )
            sources = str(len(sources_data.get("sources", [])))
            self._ui(
                lambda: self.dossier_tree.item(iid, values=(case_id, chunks, sources))
            )
        except Exception as exc:
            logger.debug("Failed to enrich dossier row for %s: %s", case_id, exc)

    def _selected_dossier(self) -> str | None:
        sel = self.dossier_tree.selection()
        return sel[0] if sel else None

    def _on_dossier_select(self, _event: Any) -> None:
        case_id = self._selected_dossier()
        if not case_id:
            return
        self._active_case = case_id
        self.chat_case_var.set(case_id)
        self._chat_case = case_id
        self._bg(self._fetch_dossier_detail, case_id)

    def _fetch_dossier_detail(self, case_id: str) -> None:
        try:
            summary = self._get(f"/api/v1/anchorum/cases/{case_id}/summary", timeout=15)
            sources_data = self._get(
                f"/api/v1/anchorum/cases/{case_id}/sources", timeout=15
            )
            lines = [
                f"Case ID:    {summary.get('case_id', case_id)}",
                f"Report ID:  {summary.get('report_id')}",
                f"Generated:  {summary.get('generated_at') or 'N/A'}",
                f"Artifacts:  {summary.get('artifact_count')}   "
                f"Entities: {summary.get('entity_count')}   "
                f"Anomalies: {summary.get('anomaly_count')}",
                f"Severity:   Critical {summary.get('critical_count')} · "
                f"High {summary.get('high_count')} · "
                f"Medium {summary.get('medium_count')} · "
                f"Low {summary.get('low_count')}",
                "",
                f"Sources ({len(sources_data.get('sources', []))}):",
            ]
            for s in sources_data.get("sources", []):
                lines.append(f"  [{s.get('source_type')}] {s.get('source', s.get('path', ''))}")
            for d in sources_data.get("extra_dirs", []):
                lines.append(f"  [attached dir] {d}")
            self._ui(self._set_text, self.dossier_detail, "\n".join(lines))
            self._ui(self._set_status, f"Loaded dossier {case_id}")
        except Exception as exc:
            self._ui(self._set_text, self.dossier_detail, f"Failed: {exc}")

    def _attach_dossier_dir(self) -> None:
        case_id = self._selected_dossier()
        if not case_id:
            self._ui(self._set_status, "Select a dossier first")
            return
        path = filedialog.askdirectory()
        if not path:
            return
        self._ui(self._set_status, f"Attaching {path} to {case_id}…")
        self._bg(self._attach_dossier_dir_bg, case_id, path)

    def _attach_dossier_dir_bg(self, case_id: str, path: str) -> None:
        try:
            self._post(
                f"/api/v1/anchorum/cases/{case_id}/sources/attach",
                {"extra_dirs": [path]},
            )
            stats = self._post(f"/api/v1/anchorum/cases/{case_id}/reindex", {})
            self._ui(
                self._set_status,
                f"Attached and indexed {stats.get('documents', 0)} documents / "
                f"{stats.get('chunks', 0)} chunks for {case_id}",
            )
        except Exception as exc:
            self._ui(self._set_status, f"Attach failed: {exc}")
        self._bg(self._load_dossiers)

    def _reindex_dossier(self) -> None:
        case_id = self._selected_dossier()
        if not case_id:
            self._ui(self._set_status, "Select a dossier to reindex")
            return
        self._ui(self._set_status, f"Reindexing {case_id}…")
        self._bg(self._reindex_dossier_bg, case_id)

    def _reindex_dossier_bg(self, case_id: str) -> None:
        try:
            stats = self._post(f"/api/v1/anchorum/cases/{case_id}/reindex", {})
            self._ui(
                self._set_status,
                f"Indexed {stats.get('documents', 0)} documents / "
                f"{stats.get('chunks', 0)} chunks for {case_id}",
            )
        except Exception as exc:
            self._ui(self._set_status, f"Reindex failed: {exc}")
        self._bg(self._load_dossiers)

    def _open_dossier_chat(self) -> None:
        case_id = self._selected_dossier()
        if not case_id:
            return
        self.chat_case_var.set(case_id)
        self._chat_case = case_id
        # Find the AI Agent tab by text and select it.
        for child in self.winfo_children():
            if isinstance(child, ttk.Notebook):
                for idx in range(child.index("end")):
                    if child.tab(idx, "text") == "AI Agent":
                        child.select(idx)
                        return

    def _delete_dossier(self) -> None:
        case_id = self._selected_dossier()
        if not case_id:
            self._ui(self._set_status, "Select a dossier to delete")
            return
        if not messagebox.askyesno(
            "Delete dossier",
            f"Delete dossier {case_id}?\n\nThis removes its report, index, and work "
            "directory. Provenance .zarc chains are kept. This cannot be undone.",
        ):
            return
        self._bg(self._delete_case_bg, case_id)

    def _load_tools_ui(self) -> None:
        self.tools_list.delete(0, tk.END)
        self._bg(self._fetch_tools)

    def _fetch_tools(self) -> None:
        try:
            data = self._get("/api/v1/anchorum/tools", timeout=15)
            tools = data.get("tools", [])
            lines = [f"{t.get('name', t['id'])} ({t['kind']})" for t in tools]
            self._ui(self.tools_list.delete, 0, tk.END)
            for line in lines:
                self._ui(self.tools_list.insert, tk.END, line)
            self._tools = tools
        except Exception as exc:
            self._ui(self.tools_list.insert, tk.END, f"Tools unavailable: {exc}")
            self._tools = []

    def _launch_tool(self) -> None:
        sel = self.tools_list.curselection()
        if not sel:
            return
        tool = self._tools[sel[0]]
        if not tool.get("enabled", True):
            messagebox.showinfo("Tool disabled", f"{tool.get('name', tool['id'])} is not enabled.")
            return
        if tool["kind"] == "url":
            import webbrowser

            webbrowser.open(tool["target"])
        elif tool["kind"] == "local_exec":
            self._ui(self._set_status, f"Launching {tool['id']}…")
            self._bg(self._launch_tool_bg, tool)

    def _launch_tool_bg(self, tool: dict) -> None:
        import subprocess

        try:
            result = subprocess.run(  # noqa: S603
                [tool["target"]],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self._ui(
                self._set_status,
                f"{tool['id']}: exit {result.returncode}",
            )
            if result.stdout:
                messagebox.showinfo(tool.get("name", tool["id"]), result.stdout[:2000])
        except Exception as exc:
            self._ui(self._set_status, f"Launch failed: {exc}")

    def _load_cases(self) -> None:
        self._ui(self._set_status, "Loading cases…")
        try:
            cases = self._get("/api/v1/anchorum/cases", timeout=15)
            self._ui(self._fill_cases, cases)
            self._ui(self._set_status, f"{len(cases)} case(s) loaded")
        except Exception as exc:
            self._ui(self._set_status, f"Failed to load cases: {exc}")

    def _on_case_select(self, _event) -> None:
        case_id = self._selected_case()
        if case_id:
            # Keep the chat focus in sync when a case is picked in Cases tab.
            self.chat_case_var.set(case_id)
            self._chat_case = case_id
            self._load_selected_summary()
            self._load_selected_anomalies()

    def _on_chat_case_change(self, _event=None) -> None:
        value = self.chat_case_var.get()
        self._chat_case = None if value == NO_CASE else value
        self._set_status(f"Chat focus: {value}")
        self._bg(self._load_knowledge)

    def _load_models(self) -> None:
        """Which AI serves this app (chat model + EMS fleet)."""
        try:
            data = self._get("/api/v1/anchorum/models", timeout=15)
            chat_model = data.get("chat_model", "?")
            self._chat_model = chat_model
            self._ui(self.model_lbl.config, {"text": f"Model: {chat_model}"})
            lines = [f"ANCHORUM chat is served by: {chat_model}", f"EMS: {data.get('ems_url')}", ""]
            if data.get("error"):
                lines.append(f"(fleet listing unavailable: {data['error']})")
            for m in data.get("models", []):
                meta = m.get("meta", {})
                marker = "  <-- serves chat" if m.get("id") == chat_model else ""
                lines.append(
                    f"{m.get('id')}  [{meta.get('backend_type', '?')}, "
                    f"{meta.get('status', '?')}]{marker}"
                )
            self._ui(self._set_text, self.sys_models, "\n".join(lines))
        except Exception as exc:
            self._ui(self.model_lbl.config, {"text": "Model: unavailable"})
            self._ui(self._set_text, self.sys_models, f"Models unavailable: {exc}")

    def _load_knowledge(self) -> None:
        """Per-case RAG index status for the current chat focus case."""
        case_id = self._chat_case
        if not case_id:
            self._ui(self.knowledge_lbl.config, {"text": "Knowledge: (no case)"})
            return
        try:
            stats = self._get(f"/api/v1/anchorum/cases/{case_id}/index", timeout=15)
            if stats.get("indexed"):
                ts = stats.get("last_indexed")
                when = time.strftime("%H:%M", time.localtime(ts)) if ts else "?"
                text = f"Knowledge: {stats['chunks']} chunks (indexed {when})"
            else:
                text = "Knowledge: not indexed — click Reindex"
            self._ui(self.knowledge_lbl.config, {"text": text})
        except Exception as exc:
            self._ui(self.knowledge_lbl.config, {"text": f"Knowledge: error ({exc})"})

    def _reindex_case(self) -> None:
        case_id = self._chat_case
        if not case_id:
            self._ui(self._set_status, "Reindex: pick a focus case first")
            return
        self._ui(self._set_status, f"Reindexing {case_id} from its files…")
        try:
            stats = self._post(f"/api/v1/anchorum/cases/{case_id}/reindex", {})
            self._ui(
                self._set_status,
                f"Indexed {stats.get('documents', 0)} documents / "
                f"{stats.get('chunks', 0)} chunks for {case_id}",
            )
        except Exception as exc:
            self._ui(self._set_status, f"Reindex failed: {exc}")
        self._load_knowledge()

    def _load_selected_summary(self) -> None:
        case_id = self._selected_case()
        if case_id:
            self._bg(self._fetch_summary, case_id)

    def _fetch_summary(self, case_id: str) -> None:
        self._ui(self._set_status, f"Loading {case_id} summary…")
        try:
            s = self._get(f"/api/v1/anchorum/cases/{case_id}/summary")
            text = (
                f"Case ID:    {s.get('case_id')}\n"
                f"Report ID:  {s.get('report_id')}\n"
                f"Generated:  {s.get('generated_at') or 'N/A'}\n"
                f"Artifacts:  {s.get('artifact_count')}   "
                f"Entities: {s.get('entity_count')}   "
                f"Anomalies: {s.get('anomaly_count')}\n"
                f"Severity:   Critical {s.get('critical_count')} · "
                f"High {s.get('high_count')} · "
                f"Medium {s.get('medium_count')} · "
                f"Low {s.get('low_count')}"
            )
            self._ui(self._set_text, self.summary_txt, text)
            self._ui(self._set_status, f"Loaded {case_id}")
        except Exception as exc:
            self._ui(self._set_text, self.summary_txt, f"Failed: {exc}")
            self._ui(self._set_status, "Error")

    def _load_selected_anomalies(self) -> None:
        case_id = self._selected_case()
        if case_id:
            self._bg(self._fetch_anomalies, case_id)

    def _fetch_anomalies(self, case_id: str) -> None:
        self._ui(self._set_status, f"Loading {case_id} anomalies…")
        try:
            data = self._get(f"/api/v1/anchorum/cases/{case_id}/anomalies", timeout=60)
            parts: list[str] = []
            for sev in SEVERITIES:
                items = data.get(sev, [])
                parts.append(f"=== {sev.upper()} ({len(items)}) ===")
                for it in items:
                    parts.append(json.dumps(it, indent=2, default=str)[:1200])
                    parts.append("")
            self._ui(self._set_text, self.anom_txt, "\n".join(parts) or "No anomalies.")
            self._ui(self._set_status, f"Loaded anomalies for {case_id}")
        except Exception as exc:
            self._ui(self._set_text, self.anom_txt, f"Failed: {exc}")

    def _load_selected_timeline(self) -> None:
        case_id = self._selected_case()
        if case_id:
            self._bg(self._fetch_timeline, case_id)

    def _fetch_timeline(self, case_id: str) -> None:
        self._ui(self._set_status, f"Loading {case_id} timeline…")
        try:
            data = self._get(f"/api/v1/anchorum/cases/{case_id}/timeline", timeout=60)
            events = data.get("timeline", [])
            lines = [json.dumps(e, default=str) for e in events]
            self._ui(self._set_text, self.timeline_txt, "\n".join(lines) or "Empty timeline.")
            self._ui(self._set_status, f"{len(events)} timeline event(s)")
        except Exception as exc:
            self._ui(self._set_text, self.timeline_txt, f"Failed: {exc}")

    def _load_selected_report(self) -> None:
        case_id = self._selected_case()
        if case_id:
            self._bg(self._fetch_report, case_id)

    def _fetch_report(self, case_id: str) -> None:
        self._ui(self._set_status, f"Loading full report {case_id}…")
        try:
            data = self._get(f"/api/v1/anchorum/cases/{case_id}", timeout=120)
            self._ui(self._set_text, self.report_txt, json.dumps(data, indent=2, default=str))
            self._ui(self._set_status, f"Loaded full report {case_id}")
        except Exception as exc:
            self._ui(self._set_text, self.report_txt, f"Failed: {exc}")

    def _export_report(self) -> None:
        case_id = self._selected_case()
        if not case_id:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile=f"{case_id}_report.json",
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        self._bg(self._export_report_bg, case_id, path)

    def _export_report_bg(self, case_id: str, path: str) -> None:
        try:
            data = self._get(f"/api/v1/anchorum/cases/{case_id}", timeout=120)
            Path(path).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            self._ui(self._set_status, f"Exported to {path}")
        except Exception as exc:
            self._ui(self._set_status, f"Export failed: {exc}")

    # -------------------------------------------------------- chat actions
    def _chat_welcome(self) -> None:
        self._append_chat(
            "meta", "System",
            "Select a case in the Cases tab, then ask questions here. "
            "'Ask Legal Dossier' answers using the live case data (findings, "
            "entities, anomalies) of the selected case. You can paste file "
            "content into the input below (Ctrl+V or right-click) to explore "
            "it with the AI — e.g. 'Copy content → chat' in the Fetch tab. "
            "Enter sends, Shift+Enter inserts a newline.",
        )

    def _send(self, mode: str) -> None:
        text = self.chat_entry.get("1.0", "end-1c").strip()
        if not text:
            return
        self.chat_entry.delete("1.0", tk.END)
        case_id = self._chat_case if mode == "legal" else None
        tag = f"You (case: {case_id})" if case_id else "You"
        self._append_chat("you", tag, text)
        self._bg(self._chat, text, mode, case_id)

    def _chat(self, text: str, mode: str, case_id: str | None) -> None:
        label = "Legal Dossier" if mode == "legal" else "Egregore"
        if case_id:
            label += f" [{case_id}]"
        self._ui(self._set_status, f"Asking {label}… (local LLM, may take a minute)")
        try:
            payload = {"message": text, "mode": mode}
            if case_id:
                payload["case_id"] = case_id
            data = self._post("/api/v1/anchorum/chat", payload)
            model = data.get("model")
            if model:
                label += f" ({model})"
            self._ui(self._append_chat, "agent", f"Egregore /{label}", data.get("content", "").strip())
            sources = data.get("sources") or []
            if sources:
                listing = "\n".join(f"  [{s['n']}] {s['source']}" for s in sources)
                self._ui(self._append_chat, "meta", "Sources", listing)
            self._ui(self._set_status, "Ready")
        except Exception as exc:
            self._ui(self._append_chat, "error", "System", f"Chat failed: {exc}")
            self._ui(self._set_status, "Chat failed")

    # ------------------------------------------------------- batch actions
    def _browse_input(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.batch_input.delete(0, tk.END)
            self.batch_input.insert(0, path)

    def _run_batch(self, async_: bool) -> None:
        input_path = self.batch_input.get().strip()
        case_id = self.batch_case.get().strip()
        if not input_path or not case_id:
            self._set_status("Batch needs both an input directory and a case ID")
            return
        fuse = self.batch_fuse.get()
        payload = {"input_path": input_path, "case_id": case_id, "operator": "desktop_app", "fuse": fuse}
        if async_:
            endpoint = "/api/v1/anchorum/batch/fuse" if fuse else "/api/v1/anchorum/batch"
        else:
            endpoint = "/api/v1/anchorum/batch/sync"
        self._bg(self._run_batch_bg, endpoint, payload, async_)

    def _run_batch_bg(self, endpoint: str, payload: dict, async_: bool) -> None:
        self._ui(self._set_status, f"Batch {payload['case_id']} started…")
        try:
            data = self._post(endpoint, payload, timeout=TIMEOUT if not async_ else 30)
            self._ui(self._set_text, self.batch_out, json.dumps(data, indent=2, default=str))
            self._ui(self._set_status, f"Batch {payload['case_id']}: {data.get('status', 'done')}")
            self._bg(self._load_cases)
            self._bg(self._load_jobs)
        except requests.HTTPError as exc:
            body = exc.response.text[:800] if exc.response is not None else str(exc)
            self._ui(self._set_text, self.batch_out, f"HTTP error: {body}")
            self._ui(self._set_status, "Batch failed")
        except Exception as exc:
            self._ui(self._set_text, self.batch_out, f"Failed: {exc}")
            self._ui(self._set_status, "Batch failed")

    # -------------------------------------------------------- jobs actions
    def _browse_job_input(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.job_input.delete(0, tk.END)
            self.job_input.insert(0, path)

    @staticmethod
    def _job_field(job: dict, *names: str, default: str = "") -> str:
        for n in names:
            v = job.get(n)
            if v is not None:
                return str(v)
        return default

    def _job_id_of(self, job: dict) -> str:
        return self._job_field(job, "job_id", "id", "batch_id")

    def _load_jobs(self) -> None:
        try:
            data = self._get(JOBS_ENDPOINT, timeout=15)
            jobs = data.get("jobs", data) if isinstance(data, dict) else data
            if isinstance(jobs, dict):
                # Server returned a single job object instead of a list.
                jobs = [jobs]
            if not isinstance(jobs, list):
                jobs = []
            self._jobs_unavailable = False
            self._ui(self._fill_jobs, jobs)
            self._ui(self._set_status, f"{len(jobs)} job(s)")
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "?"
            self._jobs_unavailable = True
            self._ui(self._fill_jobs, [])
            self._ui(self._set_text, self.job_detail,
                     f"Jobs endpoint unavailable (HTTP {code}) at {JOBS_ENDPOINT}.\n"
                     "Adjust JOBS_ENDPOINT at the top of this file if the backend route differs.\n\n"
                     "Job creation still works — it uses the /batch endpoints.")
            self._ui(self._set_status, f"Jobs list unavailable (HTTP {code})")
        except Exception as exc:
            self._jobs_unavailable = True
            self._ui(self._fill_jobs, [])
            self._ui(self._set_status, f"Failed to load jobs: {exc}")

    def _fill_jobs(self, jobs: list[dict]) -> None:
        prev = self._active_job
        self._refilling_jobs = True
        try:
            children = self.job_tree.get_children()
            if children:  # delete() with zero items is a Tcl error
                self.job_tree.delete(*children)
            self._jobs = jobs
            self._job_rows = {}
            keep: str | None = None
            for job in jobs:
                jid = self._job_id_of(job)
                status = self._job_field(job, "status", default="unknown")
                # Let the tree assign the iid: job IDs may be missing or
                # duplicated, both of which crash insert() if used as iid.
                iid = self.job_tree.insert(
                    "", tk.END,
                    values=(
                        jid,
                        self._job_field(job, "case_id"),
                        status,
                        self._job_field(job, "created_at", "submitted_at", "queued_at"),
                        self._job_field(job, "progress", "pct"),
                    ),
                    tags=(status.lower(),),
                )
                self._job_rows[iid] = job
                if jid and jid == prev:
                    keep = iid
            if keep is not None:
                self.job_tree.selection_set(keep)
                self.job_tree.see(keep)
            elif prev is not None:
                # The previously selected job is gone from the server list.
                self._active_job = None
        finally:
            self._refilling_jobs = False

    def _on_job_select(self, _event) -> None:
        if self._refilling_jobs:
            return  # programmatic re-selection during refresh, not a user click
        sel = self.job_tree.selection()
        if not sel:
            return
        job = self._job_rows.get(sel[0])
        if job is None:
            return
        self._active_job = self._job_id_of(job)
        self._set_text(self.job_detail, json.dumps(job, indent=2, default=str))
        if not self._jobs_unavailable and self._active_job:
            self._bg(self._fetch_job_detail, self._active_job)

    def _fetch_job_detail(self, job_id: str) -> None:
        try:
            data = self._get(f"{JOBS_ENDPOINT}/{job_id}", timeout=15)
            self._ui(self._set_text, self.job_detail, json.dumps(data, indent=2, default=str))
        except Exception:  # noqa: S110
            pass  # keep whatever the list payload already showed

    def _create_job(self) -> None:
        input_path = self.job_input.get().strip()
        case_id = self.job_case.get().strip()
        if not input_path or not case_id:
            self._set_status("New job needs both an input directory and a case ID")
            return
        fuse = self.job_fuse.get()
        payload = {"input_path": input_path, "case_id": case_id, "operator": "desktop_app", "fuse": fuse}
        endpoint = "/api/v1/anchorum/batch/fuse" if fuse else "/api/v1/anchorum/batch"
        self._bg(self._create_job_bg, endpoint, payload)

    def _create_job_bg(self, endpoint: str, payload: dict) -> None:
        self._ui(self._set_status, f"Creating job for {payload['case_id']}…")
        try:
            data = self._post(endpoint, payload, timeout=30)
            job_id = (data.get("job_id") or data.get("id")) if isinstance(data, dict) else None
            job_id = job_id or "?"
            self._ui(self._set_text, self.job_detail, json.dumps(data, indent=2, default=str))
            self._ui(self._set_status, f"Job {job_id} created")
            self._bg(self._load_jobs)
        except requests.HTTPError as exc:
            body = exc.response.text[:800] if exc.response is not None else str(exc)
            self._ui(self._set_text, self.job_detail, f"HTTP error: {body}")
            self._ui(self._set_status, "Job creation failed")
        except Exception as exc:
            self._ui(self._set_text, self.job_detail, f"Failed: {exc}")
            self._ui(self._set_status, "Job creation failed")

    def _delete_selected_job(self) -> None:
        sel = self.job_tree.selection()
        if not sel:
            self._set_status("Select a job to delete")
            return
        job = self._job_rows.get(sel[0], {})
        job_id = self._job_id_of(job)
        if not job_id:
            self._set_status("Selected job has no ID — cannot delete")
            return
        status = self._job_field(job, "status", default="unknown")
        if not messagebox.askyesno(
            "Delete job",
            f"Delete job {job_id}?\n\nCase: {self._job_field(job, 'case_id') or '—'}\n"
            f"Status: {status}\n\nThis cannot be undone.",
        ):
            return
        self._bg(self._delete_job_bg, job_id)

    def _delete_job_bg(self, job_id: str) -> None:
        self._ui(self._set_status, f"Deleting job {job_id}…")
        try:
            data = self._delete(f"{JOBS_ENDPOINT}/{job_id}", timeout=30)
            if self._active_job == job_id:
                self._active_job = None
            self._ui(self._set_text, self.job_detail, json.dumps(data, indent=2, default=str))
            self._ui(self._set_status, f"Job {job_id} deleted")
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "?"
            body = exc.response.text[:400] if exc.response is not None else str(exc)
            self._ui(self._set_status, f"Delete failed (HTTP {code}): {body}")
        except Exception as exc:
            self._ui(self._set_status, f"Delete failed: {exc}")
        self._load_jobs()

    def _jobs_auto_tick(self) -> None:
        if self.job_auto.get() and not self._jobs_unavailable:
            self._bg(self._load_jobs)
        self.after(JOB_AUTO_REFRESH_S * 1000, self._jobs_auto_tick)

    # ------------------------------------------------------ system actions
    @staticmethod
    def _html_to_text(html: str) -> str:
        """Crude HTML-fragment to readable text conversion."""
        txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
        txt = re.sub(r"<br\s*/?>", "\n", txt, flags=re.I)
        txt = re.sub(r"</(div|p|li|tr|h[1-6]|span|button)>", "\n", txt, flags=re.I)
        txt = re.sub(r"<[^>]+>", " ", txt)
        txt = unescape(txt)
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in txt.splitlines()]
        return "\n".join(ln for ln in lines if ln)

    def _get_text(self, path: str, timeout: int = 15) -> str:
        r = session.get(f"{BASE_URL}{path}", timeout=timeout)
        r.raise_for_status()
        return r.text

    def _load_status(self) -> None:
        try:
            data = self._get("/dashboard/debug/state", timeout=15)
            ready = self._get("/health/ready", timeout=10)
            text = (
                f"System state:   {data.get('state')}\n"
                f"Frozen:         {data.get('is_frozen')}\n"
                f"History events: {data.get('history_count')}\n"
                f"Last event:     {data.get('last_event')}\n\n"
                f"Health:         {ready.get('status')} ({ready.get('plane')})"
            )
            self._ui(self._set_text, self.sys_status, text)
            self._ui(self.health_lbl.config,
                     {"foreground": "#c01c1c" if data.get("is_frozen") else "#177a3a"})
        except Exception as exc:
            self._ui(self._set_text, self.sys_status, f"Status unavailable: {exc}")
            self._ui(self.health_lbl.config, {"foreground": "#888888"})

    def _freeze(self, do_freeze: bool) -> None:
        self._bg(self._freeze_bg, do_freeze)

    def _freeze_bg(self, do_freeze: bool) -> None:
        action = "freeze" if do_freeze else "unfreeze"
        try:
            r = session.post(
                f"{BASE_URL}/dashboard/{action}",
                timeout=15, allow_redirects=False,
            )
            self._ui(self._set_status, f"{action.capitalize()}: HTTP {r.status_code}")
        except Exception as exc:
            self._ui(self._set_status, f"{action.capitalize()} failed: {exc}")
        self._load_status()
        self._load_audit()

    def _load_key_health(self) -> None:
        try:
            html = self._get_text("/dashboard/key-health", timeout=15)
            self._ui(self._set_text, self.sys_keys, self._html_to_text(html))
        except Exception as exc:
            self._ui(self._set_text, self.sys_keys, f"Unavailable: {exc}")

    def _load_ci_health(self) -> None:
        try:
            html = self._get_text("/dashboard/ci-health", timeout=15)
            self._ui(self._set_text, self.sys_ci, self._html_to_text(html))
        except Exception as exc:
            self._ui(self._set_text, self.sys_ci, f"Unavailable: {exc}")

    def _load_audit(self) -> None:
        try:
            html = self._get_text("/dashboard/audit", timeout=15)
            text = self._html_to_text(html)
            self._ui(self._set_text, self.sys_audit, text or "No audit events yet.")
        except Exception as exc:
            self._ui(self._set_text, self.sys_audit, f"Unavailable: {exc}")


if __name__ == "__main__":
    AnchorumApp().mainloop()
