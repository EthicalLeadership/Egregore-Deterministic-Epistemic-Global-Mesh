#!/usr/bin/env python3
"""ANCHORUM Desktop — native Tkinter client for the Egregore ANCHORUM site.

Full toolset, no browser. Talks directly to the local API on 127.0.0.1:8080.
Run:  .venv/bin/python anchorum_desktop.py
"""

from __future__ import annotations

import json
import queue
import re
import threading
import tkinter as tk
from html import unescape
from pathlib import Path
from tkinter import filedialog, ttk

import requests

BASE_URL = "http://127.0.0.1:8080"
API_KEY = (Path(__file__).parent / "secrets" / "api_key.hex").read_text().strip()
HEADERS = {"X-API-Key": API_KEY, "Accept": "application/json"}
TIMEOUT = 180  # local LLM can be slow

SEVERITIES = ("critical", "high", "medium", "low", "info")


class AnchorumApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ANCHORUM — Legal Dossier AI")
        self.geometry("1150x720")
        self.minsize(900, 560)

        self._q: queue.Queue = queue.Queue()
        self._cases: list[str] = []
        self._active_case: str | None = None
        self._build_ui()
        self.after(100, self._poll_queue)
        self._bg(self._load_cases)
        self._bg(self._load_status)
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
        self._build_chat_tab(nb)
        self._build_batch_tab(nb)
        self._build_system_tab(nb)

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
        ttk.Button(left, text="Refresh", command=lambda: self._bg(self._load_cases)).pack(fill=tk.X, pady=(4, 0))

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

    def _build_chat_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="AI Agent")

        self.chat_log = tk.Text(tab, wrap=tk.WORD, state=tk.DISABLED)
        self.chat_log.pack(fill=tk.BOTH, expand=True)
        self.chat_log.tag_config("you", foreground="#1a6ed1")
        self.chat_log.tag_config("agent", foreground="#177a3a")
        self.chat_log.tag_config("error", foreground="#c01c1c")
        self.chat_log.tag_config("meta", foreground="#888888")

        controls = ttk.Frame(tab)
        controls.pack(fill=tk.X, pady=(6, 0))
        self.chat_entry = ttk.Entry(controls)
        self.chat_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.chat_entry.bind("<Return>", lambda _e: self._send("legal"))
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
        self.batch_input.grid(row=0, column=1, sticky=tk.EW, padx=6)
        ttk.Button(form, text="Browse…", command=self._browse_input).grid(row=0, column=2)

        ttk.Label(form, text="Case ID:").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        self.batch_case = ttk.Entry(form, width=40)
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

        f2 = ttk.LabelFrame(cols, text="Key Health / CI Health", padding=4)
        cols.add(f2, weight=1)
        self.sys_keys = self._make_ro_text_frame(f2)

        f3 = ttk.LabelFrame(cols, text="Freeze Audit Log", padding=4)
        cols.add(f3, weight=1)
        self.sys_audit = self._make_ro_text_frame(f3)

        btns = ttk.Frame(tab)
        btns.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btns, text="Load Key Health", command=lambda: self._bg(self._load_key_health)).pack(side=tk.LEFT)
        ttk.Button(btns, text="Load CI Health", command=lambda: self._bg(self._load_ci_health)).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns, text="Load Audit Log", command=lambda: self._bg(self._load_audit)).pack(side=tk.LEFT, padx=(6, 0))

    # ----------------------------------------------------------- UI helpers
    def _make_ro_text(self, parent, tab_label: str | None, height: int = 10) -> tk.Text:
        frame = ttk.Frame(parent, padding=4)
        txt = tk.Text(frame, wrap=tk.WORD, state=tk.DISABLED, height=height)
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
        txt = tk.Text(frame, wrap=tk.WORD, state=tk.DISABLED)
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=txt.yview)
        txt.configure(yscrollcommand=scroll.set)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return txt

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.config(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, text)
        widget.config(state=tk.DISABLED)

    # ------------------------------------------------------------ plumbing
    def _bg(self, fn, *args) -> None:
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _poll_queue(self) -> None:
        try:
            while True:
                fn, args = self._q.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _ui(self, fn, *args) -> None:
        self._q.put((fn, args))

    def _set_status(self, text: str) -> None:
        self.status.config(text=text)

    def _append_chat(self, tag: str, who: str, text: str) -> None:
        self.chat_log.config(state=tk.NORMAL)
        self.chat_log.insert(tk.END, f"{who}\n", "meta")
        self.chat_log.insert(tk.END, f"{text}\n\n", tag)
        self.chat_log.config(state=tk.DISABLED)
        self.chat_log.see(tk.END)

    def _clear_chat(self) -> None:
        self._set_text(self.chat_log, "")

    def _fill_cases(self, cases: list[str]) -> None:
        self._cases = cases
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
        r = requests.get(f"{BASE_URL}{path}", headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict, timeout: int = TIMEOUT):
        r = requests.post(
            f"{BASE_URL}{path}",
            headers={**HEADERS, "Content-Type": "application/json"},
            json=payload, timeout=timeout,
        )
        r.raise_for_status()
        return r.json()

    def _refresh_all(self) -> None:
        self._bg(self._load_cases)
        self._bg(self._load_status)
        self._bg(self._load_key_health)
        self._bg(self._load_audit)

    # ------------------------------------------------------- cases actions
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
            self._load_selected_summary()
            self._load_selected_anomalies()

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
            "entities, anomalies) of the selected case.",
        )

    def _send(self, mode: str) -> None:
        text = self.chat_entry.get().strip()
        if not text:
            return
        self.chat_entry.delete(0, tk.END)
        case_id = self._active_case if mode == "legal" else None
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
            self._ui(self._append_chat, "agent", f"Egregore /{label}", data.get("content", "").strip())
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
        except requests.HTTPError as exc:
            body = exc.response.text[:800] if exc.response is not None else str(exc)
            self._ui(self._set_text, self.batch_out, f"HTTP error: {body}")
            self._ui(self._set_status, "Batch failed")
        except Exception as exc:
            self._ui(self._set_text, self.batch_out, f"Failed: {exc}")
            self._ui(self._set_status, "Batch failed")

    # ------------------------------------------------------ system actions
    @staticmethod
    def _html_to_text(html: str) -> str:
        """Crude HTML-fragment to readable text conversion."""
        txt = re.sub(r"<(script|style)[^>]*>.*?</\\1>", "", html, flags=re.S | re.I)
        txt = re.sub(r"<br\s*/?>", "\n", txt, flags=re.I)
        txt = re.sub(r"</(div|p|li|tr|h[1-6]|span|button)>", "\n", txt, flags=re.I)
        txt = re.sub(r"<[^>]+>", " ", txt)
        txt = unescape(txt)
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in txt.splitlines()]
        return "\n".join(ln for ln in lines if ln)

    def _get_text(self, path: str, timeout: int = 15) -> str:
        r = requests.get(f"{BASE_URL}{path}", headers=HEADERS, timeout=timeout)
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
            r = requests.post(
                f"{BASE_URL}/dashboard/{action}",
                headers=HEADERS, timeout=15, allow_redirects=False,
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
            self._ui(self._set_text, self.sys_keys, self._html_to_text(html))
        except Exception as exc:
            self._ui(self._set_text, self.sys_keys, f"Unavailable: {exc}")

    def _load_audit(self) -> None:
        try:
            html = self._get_text("/dashboard/audit", timeout=15)
            text = self._html_to_text(html)
            self._ui(self._set_text, self.sys_audit, text or "No audit events yet.")
        except Exception as exc:
            self._ui(self._set_text, self.sys_audit, f"Unavailable: {exc}")


if __name__ == "__main__":
    AnchorumApp().mainloop()
