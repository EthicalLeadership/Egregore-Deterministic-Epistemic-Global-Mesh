"""Factory operator tab for the ANCHORUM desktop app.

Zero HTTP. Reads the factory's telemetry JSONL and governance files directly
from disk — the app shows what the factory wrote; if the factory is down, the
UI shows stale data honestly (timestamped), never a spinner.

Read-only by design (v1). Inviolable rule: every number clicks through to
the raw telemetry event it came from.
"""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent
TELEMETRY_DIR = REPO_ROOT / "report" / "factory_telemetry"
DECISION_TABLE = REPO_ROOT / "config" / "factory_decision_table.json"
WEEKLY_GLOB = "factory_weekly_*.json"

# Event fields rendered per surface. Anything not shown here is visible in the
# raw-event pane (click-through rule).
RUN_COLS = ("run", "mode", "state", "elapsed_s", "tokens", "reworks", "vram_min")
EVENT_COLS = ("seq", "type", "station", "detail")
QC_COLS = ("time", "verdict", "tier", "confidence", "violations")
RES_COLS = ("time", "station", "vram_free_mb", "backend", "elapsed_ms")


def _short(rid: str | None) -> str:
    return (rid or "?")[:8]


class FactoryTab(ttk.Frame):
    """Six-surface factory interface (read-only)."""

    def __init__(self, parent: ttk.Notebook, status_setter) -> None:
        super().__init__(parent, padding=6)
        self._set_status = status_setter
        self._offsets: dict[Path, int] = {}
        self._events: list[dict[str, Any]] = []
        self._runs: dict[str, list[dict[str, Any]]] = {}
        self._build()
        self._reload_all()
        self.after(2000, self._poll)

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True)

        nb.add(self._build_line(nb), text="Line")
        nb.add(self._build_qc(nb), text="QC / Exceptions")
        nb.add(self._build_decision(nb), text="Decision")
        nb.add(self._build_residency(nb), text="Residency")

    def _tree(self, parent, cols, height=10) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=cols, show="headings", height=height)
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=110, anchor=tk.W)
        scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return tree

    def _build_line(self, nb: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(nb, padding=4)
        top = ttk.Frame(tab)
        top.pack(fill=tk.BOTH, expand=True)
        self.runs_tree = self._tree(top, RUN_COLS, height=9)
        self.runs_tree.bind("<<TreeviewSelect>>", self._on_run_select)

        mid = ttk.LabelFrame(tab, text="Run events (click an event for raw JSON)", padding=4)
        mid.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.events_tree = self._tree(mid, EVENT_COLS, height=8)
        self.events_tree.bind("<<TreeviewSelect>>", self._on_event_select)

        bottom = ttk.LabelFrame(tab, text="Raw telemetry event", padding=4)
        bottom.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.raw_txt = tk.Text(bottom, wrap=tk.NONE, height=8, state=tk.DISABLED)
        self.raw_txt.pack(fill=tk.BOTH, expand=True)
        return tab

    def _build_qc(self, nb: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(nb, padding=4)
        self.qc_tree = self._tree(tab, QC_COLS, height=12)
        self.qc_tree.bind("<<TreeviewSelect>>", self._on_qc_select)
        bottom = ttk.LabelFrame(tab, text="Verdict detail (raw event)", padding=4)
        bottom.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.qc_raw = tk.Text(bottom, wrap=tk.NONE, height=10, state=tk.DISABLED)
        self.qc_raw.pack(fill=tk.BOTH, expand=True)
        return tab

    def _build_decision(self, nb: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(nb, padding=4)
        self.decision_txt = tk.Text(tab, wrap=tk.WORD, state=tk.DISABLED)
        self.decision_txt.pack(fill=tk.BOTH, expand=True)
        ttk.Button(tab, text="Refresh", command=self._render_decision).pack(anchor=tk.E, pady=(4, 0))
        return tab

    def _build_residency(self, nb: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(nb, padding=4)
        self.res_summary = ttk.Label(tab, text="")
        self.res_summary.pack(anchor=tk.W)
        self.res_tree = self._tree(tab, RES_COLS, height=14)
        self.res_tree.bind("<<TreeviewSelect>>", self._on_res_select)
        bottom = ttk.LabelFrame(tab, text="Raw event", padding=4)
        bottom.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.res_raw = tk.Text(bottom, wrap=tk.NONE, height=6, state=tk.DISABLED)
        self.res_raw.pack(fill=tk.BOTH, expand=True)
        return tab

    # ------------------------------------------------------------- loading
    def _telemetry_files(self) -> list[Path]:
        if not TELEMETRY_DIR.exists():
            return []
        return sorted(TELEMETRY_DIR.glob("factory_*.jsonl"))

    def _reload_all(self) -> None:
        self._offsets.clear()
        self._events.clear()
        self._runs.clear()
        for path in self._telemetry_files():
            self._offsets[path] = 0
        self._poll(first=True)
        self._render_decision()

    def _poll(self, first: bool = False) -> None:
        new_events = 0
        for path in self._telemetry_files():
            offset = self._offsets.get(path, 0)
            size = path.stat().st_size
            if size < offset:
                offset = 0  # rotated/truncated
            if size == offset:
                continue
            with path.open("r", encoding="utf-8") as f:
                f.seek(offset)
                chunk = f.read()
                self._offsets[path] = f.tell()
            for line in chunk.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue  # partial line at write boundary; picked up next poll
                self._events.append(ev)
                rid = ev.get("run_id")
                if rid:
                    self._runs.setdefault(rid, []).append(ev)
                new_events += 1
        if new_events or first:
            self._refresh_views()
        self.after(2000, self._poll)

    # -------------------------------------------------------------- renders
    def _refresh_views(self) -> None:
        self._render_runs()
        self._render_qc()
        self._render_residency()
        n = len(self._runs)
        last = self._events[-1]["ts"][:19] if self._events else "never"
        self._set_status(f"Factory: {n} runs, {len(self._events)} events (last {last} UTC)")

    def _run_row(self, rid: str, events: list[dict[str, Any]]) -> tuple:
        outcome = next((e for e in events if e["event_type"] == "factory.run.outcome"), {})
        env = next((e for e in events if e["event_type"] == "factory.envelope.in"), {})
        stations = [e for e in events if e["event_type"] == "factory.station"]
        qc = outcome.get("qc") or {}
        vrams = [e["vram_free_mb"] for e in stations if e.get("vram_free_mb") is not None]
        return (
            _short(rid),
            env.get("mode") or outcome.get("mode", "?"),
            qc.get("terminal_state", "…" if not outcome else "?"),
            round(outcome.get("total_elapsed_ms", 0) / 1000, 1),
            outcome.get("total_tokens", ""),
            qc.get("reworks", ""),
            min(vrams) if vrams else "",
        )

    def _render_runs(self) -> None:
        selected = {self.runs_tree.item(i, "values")[0] for i in self.runs_tree.selection()}
        self.runs_tree.delete(*self.runs_tree.get_children())
        ordered = sorted(
            self._runs.items(),
            key=lambda kv: max(e.get("ts_ns", 0) for e in kv[1]),
            reverse=True,
        )
        for rid, events in ordered:
            iid = self.runs_tree.insert("", tk.END, values=self._run_row(rid, events))
            if _short(rid) in selected:
                self.runs_tree.selection_set(iid)

    def _event_row(self, ev: dict[str, Any]) -> tuple:
        et = ev.get("event_type", "?")
        detail = ""
        if et == "factory.inference":
            detail = f"tok={ev.get('total_tokens')} m={''.join('1' if ev.get(f'm{n}') else '0' for n in range(1, 5))}"
        elif et == "factory.qc.verdict":
            detail = f"{ev.get('verdict')} conf={ev.get('confidence')}"
        elif et == "factory.run.outcome":
            detail = f"ok={ev.get('ok')} tok={ev.get('total_tokens')}"
        elif et == "factory.station":
            detail = f"{ev.get('elapsed_ms')}ms tok={ev.get('tokens')}"
        return (ev.get("seq_no", ""), et.replace("factory.", ""), ev.get("station", ""), detail)

    def _render_qc(self) -> None:
        self.qc_tree.delete(*self.qc_tree.get_children())
        verdicts = [e for e in self._events if e["event_type"] == "factory.qc.verdict"]
        for ev in reversed(verdicts[-200:]):
            self.qc_tree.insert("", tk.END, values=(
                str(ev.get("ts", ""))[11:19],
                ev.get("verdict"),
                ev.get("tier"),
                ev.get("confidence"),
                len(ev.get("violations", [])),
            ))

    def _render_residency(self) -> None:
        self.res_tree.delete(*self.res_tree.get_children())
        stations = [
            e for e in self._events
            if e["event_type"] == "factory.station" and e.get("vram_free_mb") is not None
        ]
        for ev in reversed(stations[-200:]):
            self.res_tree.insert("", tk.END, values=(
                str(ev.get("ts", ""))[11:19],
                ev.get("station"),
                ev.get("vram_free_mb"),
                ev.get("backend", ""),
                ev.get("elapsed_ms"),
            ))
        if stations:
            vals = [e["vram_free_mb"] for e in stations]
            self.res_summary.config(
                text=f"VRAM free MB — min {min(vals)}, latest {vals[-1]}, samples {len(vals)}"
            )
        else:
            self.res_summary.config(text="No station telemetry with VRAM data yet.")

    def _render_decision(self) -> None:
        lines: list[str] = []
        if DECISION_TABLE.exists():
            table = json.loads(DECISION_TABLE.read_text(encoding="utf-8"))
            lines.append("PRE-REGISTERED DECISION TABLE")
            lines.append(f"  registered: {table.get('registered_at')} (commit {table.get('registered_commit')})")
            lines.append(f"  regime cutoff: {table.get('regime_cutoff', {}).get('timestamp_utc')}")
            lines.append(f"  minimum sample: {table.get('minimum_sample', {}).get('min_fail_events')} FAIL events")
            lines.append("")
            for rule in table.get("decision", []):
                lines.append(f"  IF {rule['outcome']}")
                lines.append(f"    -> {rule['verdict']}")
            lines.append("")
        weeklies = sorted((REPO_ROOT / "report").glob(WEEKLY_GLOB))
        if weeklies:
            latest = json.loads(weeklies[-1].read_text(encoding="utf-8"))
            decision = latest.get("decision", {})
            lines.append(f"LATEST WEEKLY REPORT: {weeklies[-1].name}")
            lines.append(f"  verdict: {decision.get('verdict')}")
            lines.append(f"  action:  {decision.get('action')}")
            lines.append(f"  FAIL events (post-cutoff): {decision.get('fail_events')}")
            families = decision.get("families") or {}
            if families:
                lines.append("  families:")
                for fam, count in families.items():
                    lines.append(f"    {fam}: {count}")
        else:
            lines.append("No weekly report yet.")
        self._set_text(self.decision_txt, "\n".join(lines))

    # -------------------------------------------------------------- events
    def _selected_run_events(self) -> list[dict[str, Any]]:
        sel = self.runs_tree.selection()
        if not sel:
            return []
        rid_short = self.runs_tree.item(sel[0], "values")[0]
        for rid, events in self._runs.items():
            if _short(rid) == rid_short:
                return events
        return []

    def _on_run_select(self, _e) -> None:
        events = self._selected_run_events()
        self.events_tree.delete(*self.events_tree.get_children())
        for i, ev in enumerate(events):
            self.events_tree.insert("", tk.END, iid=str(i), values=self._event_row(ev))

    def _on_event_select(self, _e) -> None:
        sel = self.events_tree.selection()
        if not sel:
            return
        events = self._selected_run_events()
        idx = int(sel[0])
        if 0 <= idx < len(events):
            self._set_text(self.raw_txt, json.dumps(events[idx], indent=2, default=str))

    def _on_qc_select(self, _e) -> None:
        sel = self.qc_tree.selection()
        if not sel:
            return
        verdicts = [e for e in self._events if e["event_type"] == "factory.qc.verdict"]
        shown = list(reversed(verdicts[-200:]))
        idx = self.qc_tree.index(sel[0])
        if 0 <= idx < len(shown):
            self._set_text(self.qc_raw, json.dumps(shown[idx], indent=2, default=str))

    def _on_res_select(self, _e) -> None:
        sel = self.res_tree.selection()
        if not sel:
            return
        stations = [
            e for e in self._events
            if e["event_type"] == "factory.station" and e.get("vram_free_mb") is not None
        ]
        shown = list(reversed(stations[-200:]))
        idx = self.res_tree.index(sel[0])
        if 0 <= idx < len(shown):
            self._set_text(self.res_raw, json.dumps(shown[idx], indent=2, default=str))

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.config(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, text)
        widget.config(state=tk.DISABLED)
