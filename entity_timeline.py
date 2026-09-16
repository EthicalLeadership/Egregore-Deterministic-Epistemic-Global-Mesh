#!/usr/bin/env python3
"""Entity Timeline — canvas view of ANCHORUM case events.

Dots on a time axis plus a read-only detail pane.

    view = EntityTimeline(parent)
    view.set_events(events)             # fits only on the first load
    view.set_events(events, fit=True)   # explicit refit
    view.fit_to_events()                # Fit button / double-click

``set_events`` auto-fits on the first data load only, so refreshes keep the
operator's viewport, and the selection overlay is drawn beneath the event
dots. Text handling is injected through ``text_adapter`` so this module stays
importable without the desktop layer — the app passes a ``ui_text``-backed
adapter. The pure event and timestamp logic lives in :mod:`timeline_kernel`,
whose names are re-exported here for existing callers.
"""

from __future__ import annotations

import json
import tkinter as tk
from datetime import UTC, datetime
from collections.abc import Iterable
from tkinter import ttk
from typing import Any

from timeline_kernel import (
    ENTITY_FIELD_RANKING,
    LABEL_FIELD_RANKING,
    TIMESTAMP_FIELD_RANKING,
    TimelineEvent,
    detect_entity,
    detect_label,
    detect_timestamp,
    format_timestamp,
    normalize_events,
    parse_timestamp,
)

# --------------------------------------------------------------------------
# Geometry / palette
# --------------------------------------------------------------------------

PAD_LEFT = 18
PAD_RIGHT = 18
PAD_TOP = 26
PAD_BOTTOM = 30

LANE_HEIGHT = 26
LANE_SOFT_CAP = 8
DOT_RADIUS = 4
DOT_SELECTED_RADIUS = 6
ACTIVE_RADIUS = DOT_RADIUS + 3

CLICK_TOLERANCE_PX = 9.0
DRAG_THRESHOLD_PX = 4.0

BACKGROUND = "#101418"
AXIS_COLOUR = "#2a3442"
TICK_COLOUR = "#4b5563"
TICK_TEXT = "#7d8ba3"
EMPTY_TEXT = "#6b7280"
DOT_FILL = "#22d3ee"
DOT_SELECTED = "#f97316"
DOT_ACTIVE = "#facc15"
LABEL_TEXT = "#e5e7eb"
HOVER_TEXT = "#9ca3af"
OVERLAY_FILL = "#050810"
OVERLAY_STIPPLE = "gray50"
OVERLAY_EDGE = "#22d3ee"


# --------------------------------------------------------------------------
# Text adapters
# --------------------------------------------------------------------------


class _DefaultTextAdapter:
    """UI-agnostic fallback for headless and kernel-level callers."""

    @staticmethod
    def make_readonly_copyable(widget: tk.Text) -> tk.Text:
        return widget

    @staticmethod
    def install_context_menu(widget: tk.Widget) -> tk.Menu | None:
        return None

    @staticmethod
    def set_text(widget: tk.Text, text: str) -> None:
        widget.delete("1.0", tk.END)
        if text:
            widget.insert(tk.END, text)


class TextAdapter:
    """Contract for the app-layer text adapter used by the Tk UI."""

    @staticmethod
    def make_readonly_copyable(widget: tk.Text) -> tk.Text:
        raise NotImplementedError

    @staticmethod
    def install_context_menu(widget: tk.Widget) -> tk.Menu | None:
        raise NotImplementedError

    @staticmethod
    def set_text(widget: tk.Text, text: str) -> None:
        raise NotImplementedError


# --------------------------------------------------------------------------
# Widget
# --------------------------------------------------------------------------

TICK_COUNT = 4


def _tick_label(stamp: float, span: float) -> str:
    moment = datetime.fromtimestamp(stamp, tz=UTC)
    if span < 86_400:
        return moment.strftime("%H:%M")
    if span < 86_400 * 60:
        return moment.strftime("%d %b")
    if span < 86_400 * 365:
        return moment.strftime("%b %Y")
    return moment.strftime("%Y")


class EntityTimeline(ttk.Frame):
    """Canvas timeline of case events with a copyable detail pane.

    ``detail`` (a :class:`tk.Text`) is exposed so callers can clear it with the
    same ``ui_text`` helpers used elsewhere in the desktop app.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        detail_height: int = 9,
        text_adapter: type[TextAdapter] | None = None,
    ) -> None:
        super().__init__(parent, padding=4)
        self._text_adapter = text_adapter or _DefaultTextAdapter
        self._events: list[TimelineEvent] = []
        self._has_fitted = False
        self._view: tuple[float, float] | None = None
        self._selected: TimelineEvent | None = None
        self._active: TimelineEvent | None = None
        self._range: tuple[float, float] | None = None
        self._drag_anchor: float | None = None
        self._drag_start_x: int = 0
        self._dot_items: dict[int, int] = {}
        self._build_ui(detail_height)

    # ---------------------------------------------------------------- build
    def _build_ui(self, detail_height: int) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X)

        ttk.Label(
            bar, text="Entity Timeline", font=("TkDefaultFont", 10, "bold")
        ).pack(side=tk.LEFT)
        self._count_lbl = ttk.Label(bar, text="", foreground="#888888")
        self._count_lbl.pack(side=tk.LEFT, padx=(8, 0))
        self._hover_lbl = ttk.Label(bar, text="", foreground=HOVER_TEXT)
        self._hover_lbl.pack(side=tk.LEFT, padx=(12, 0))

        ttk.Button(bar, text="Fit", command=self.fit_to_events).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Clear range", command=self.clear_range).pack(
            side=tk.RIGHT, padx=(0, 6)
        )

        paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        canvas_frame = ttk.Frame(paned)
        paned.add(canvas_frame, weight=3)
        self._canvas = tk.Canvas(
            canvas_frame,
            background=BACKGROUND,
            highlightthickness=0,
            height=220,
        )
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._canvas.bind("<Configure>", self._on_configure)
        self._canvas.bind("<Button-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._canvas.bind("<Double-Button-1>", self._on_double_click)
        self._canvas.bind("<Motion>", self._on_motion)
        self._canvas.bind("<Leave>", self._on_leave)

        detail_frame = ttk.LabelFrame(paned, text="Selected event", padding=4)
        paned.add(detail_frame, weight=2)
        self.detail = self._text_adapter.make_readonly_copyable(
            tk.Text(detail_frame, wrap=tk.WORD, height=detail_height)
        )
        self._text_adapter.install_context_menu(self.detail)
        scroll = ttk.Scrollbar(
            detail_frame, orient=tk.VERTICAL, command=self.detail.yview
        )
        self.detail.configure(yscrollcommand=scroll.set)
        self.detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    # ----------------------------------------------------------- public API
    def set_events(
        self, events: Iterable[Any] | None, fit: bool | None = None
    ) -> None:
        """Replace the displayed events.

        ``fit=None`` (the default) fits the viewport only on the first data
        load; later refreshes preserve the operator's current view, so a
        refresh never yanks the view. Pass ``fit=True`` to force a refit, or
        ``fit=False`` to never refit automatically.
        """
        self._events = normalize_events(events or ())
        if not any(ev.timestamp is not None for ev in self._events):
            self._view = None
            self._has_fitted = False
        self._selected = None
        self._active = None
        self._range = None
        distinct = len({ev.entity for ev in self._events})
        lanes = self._lane_count(*self._plot_bounds()[2:])
        suffix = f" · {distinct} entities"
        if distinct > lanes:
            suffix += " (lanes merged)"
        self._count_lbl.config(text=f"{len(self._events)} event(s){suffix}")
        self._hover_lbl.config(text="")

        if fit is None:
            fit = not self._has_fitted or self._view is None
        if fit:
            self.fit_to_events()
        else:
            self._set_detail("")
            self._redraw()

    def clear(self) -> None:
        """Drop every event and reset the fit state."""
        self._events = []
        self._selected = None
        self._active = None
        self._range = None
        self._view = None
        self._has_fitted = False
        self._count_lbl.config(text="")
        self._hover_lbl.config(text="")
        self._set_detail("")
        self._redraw()

    def fit_to_events(self) -> None:
        """Reset the viewport to span every dated event."""
        stamps = [ev.timestamp for ev in self._events if ev.timestamp is not None]
        if stamps:
            low = min(stamps)
            high = max(stamps)
            if high - low < 1.0:
                high = low + 1.0
            pad = (high - low) * 0.03
            self._view = (low - pad, high + pad)
            # Only a fit over real data counts as the initial fit.
            self._has_fitted = True
        else:
            self._view = None
        self._redraw()

    def clear_range(self) -> None:
        """Remove the selection range overlay."""
        if self._range is None:
            return
        self._range = None
        self._redraw()

    def events(self) -> list[TimelineEvent]:
        """Return the normalised events currently displayed."""
        return list(self._events)

    # ------------------------------------------------------------ geometry
    def _plot_bounds(self) -> tuple[float, float, float, float]:
        width = max(int(self._canvas.winfo_width()), 1)
        height = max(int(self._canvas.winfo_height()), 1)
        left = float(PAD_LEFT)
        right = float(max(PAD_LEFT + 1, width - PAD_RIGHT))
        top = float(PAD_TOP)
        bottom = float(max(PAD_TOP + 1, height - PAD_BOTTOM))
        return left, right, top, bottom

    def _effective_view(self) -> tuple[float, float]:
        if self._view is not None:
            return self._view
        stamps = [ev.timestamp for ev in self._events if ev.timestamp is not None]
        if not stamps:
            return 0.0, 1.0
        low = min(stamps)
        high = max(stamps)
        if high - low < 1.0:
            high = low + 1.0
        return low, high

    def _x_for(self, timestamp: float) -> float:
        left, right, _, _ = self._plot_bounds()
        start, end = self._effective_view()
        span = end - start
        if span <= 0:
            return (left + right) / 2.0
        ratio = (timestamp - start) / span
        return left + ratio * (right - left)

    def _timestamp_for_x(self, x: float) -> float:
        left, right, _, _ = self._plot_bounds()
        start, end = self._effective_view()
        width = right - left
        if width <= 0:
            return start
        ratio = (x - left) / width
        return start + ratio * (end - start)

    def _lane_count(self, top: float, bottom: float) -> int:
        available = max(1.0, bottom - top)
        return max(1, min(LANE_SOFT_CAP, int(available // LANE_HEIGHT)))

    # ------------------------------------------------------------- drawing
    def _redraw(self) -> None:
        canvas = self._canvas
        canvas.delete("all")
        left, right, top, bottom = self._plot_bounds()

        if not self._events:
            canvas.create_text(
                (left + right) / 2.0,
                (top + bottom) / 2.0,
                text="No timeline events",
                fill=EMPTY_TEXT,
            )
            return

        # The overlay is drawn first and lowered afterwards so the event dots
        # always render on top of the selection shading.
        self._draw_selection_overlay(top, bottom)
        self._draw_axis(left, right, top, bottom)
        self._draw_events(left, right, top, bottom)
        canvas.tag_lower("overlay")

    def _draw_selection_overlay(self, top: float, bottom: float) -> None:
        if self._range is None:
            return
        start, end = self._range
        left, right, _, _ = self._plot_bounds()

        x0 = min(max(self._x_for(start), left), right)
        x1 = min(max(self._x_for(end),   left), right)
        if x1 < x0:
            x0, x1 = x1, x0

        canvas = self._canvas
        if x0 > left:
            canvas.create_rectangle(
                left, top, x0, bottom,
                fill=OVERLAY_FILL, outline="", stipple=OVERLAY_STIPPLE,
                tags="overlay",
            )
        if x1 < right:
            canvas.create_rectangle(
                x1, top, right, bottom,
                fill=OVERLAY_FILL, outline="", stipple=OVERLAY_STIPPLE,
                tags="overlay",
            )
        if x1 > x0:
            canvas.create_rectangle(
                x0, top, x1, bottom, outline=OVERLAY_EDGE, tags="overlay",
            )

    def _draw_axis(
        self, left: float, right: float, top: float, bottom: float
    ) -> None:
        canvas = self._canvas
        canvas.create_line(left, bottom, right, bottom, fill=AXIS_COLOUR, tags="axis")
        start, end = self._effective_view()
        span = end - start
        if span <= 0:
            return
        for step in range(TICK_COUNT + 1):
            ratio = step / TICK_COUNT
            x = left + ratio * (right - left)
            stamp = start + ratio * span
            canvas.create_line(x, bottom, x, bottom + 4, fill=TICK_COLOUR, tags="axis")
            canvas.create_text(
                x, bottom + 14,
                text=_tick_label(stamp, span),
                fill=TICK_TEXT,
                font=("TkDefaultFont", 8),
                tags="axis",
            )

    def _draw_events(
        self, left: float, right: float, top: float, bottom: float
    ) -> None:
        canvas = self._canvas
        lanes = self._lane_count(top, bottom)
        entity_order: dict[str, int] = {}
        for event in self._events:
            entity_order.setdefault(event.entity, len(entity_order))

        self._dot_items = {}
        undated_seen: dict[int, int] = {}
        for event in self._events:
            stamp = event.timestamp
            order = entity_order[event.entity]
            lane = order % lanes
            cycle = order // lanes
            if stamp is None:
                k = undated_seen.get(lane, 0)
                undated_seen[lane] = k + 1
                x = left + 6.0
                y = bottom - LANE_HEIGHT * (lane + 0.5) - k * (DOT_RADIUS + 2)
                outline = EMPTY_TEXT
            else:
                x = self._x_for(stamp)
                y = bottom - LANE_HEIGHT * (lane + 0.5) + cycle * 3
                outline = ""

            selected = self._selected is not None and event.index == self._selected.index
            active = self._active is not None and event.index == self._active.index
            radius = DOT_RADIUS
            colour = DOT_FILL
            if active and not selected:
                radius = ACTIVE_RADIUS
                colour = DOT_ACTIVE
            if selected:
                radius = DOT_SELECTED_RADIUS
                colour = DOT_SELECTED

            item = canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                fill=colour, outline=outline, width=1, tags="dot",
            )
            self._dot_items[item] = event.index

            if selected:
                canvas.create_text(
                    x, y - radius - 9,
                    text=event.label,
                    fill=LABEL_TEXT,
                    font=("TkDefaultFont", 8),
                    tags="label",
                )

    # --------------------------------------------------------- interaction
    def _on_configure(self, _event: tk.Event) -> None:
        self._redraw()

    def _on_press(self, event: tk.Event) -> None:
        self._drag_anchor = self._timestamp_for_x(event.x)
        self._drag_start_x = event.x

    def _on_drag(self, event: tk.Event) -> None:
        if self._drag_anchor is None:
            return
        if abs(event.x - self._drag_start_x) < DRAG_THRESHOLD_PX:
            return
        other = self._timestamp_for_x(event.x)
        self._range = (min(self._drag_anchor, other), max(self._drag_anchor, other))
        self._redraw_overlay_only()

    def _on_release(self, event: tk.Event) -> None:
        if self._drag_anchor is None:
            return
        dragged = abs(event.x - self._drag_start_x) >= DRAG_THRESHOLD_PX
        self._drag_anchor = None
        if dragged:
            self._redraw()
            return
        # A plain click clears the range and picks the nearest event.
        self._range = None
        self._select_nearest(event.x, event.y)
        self._redraw()

    def _on_double_click(self, _event: tk.Event) -> str:
        # Double-clicking the canvas is the quickest way to refit the view.
        # The click that preceded this double-click may have selected a
        # nearby event; refitting is a reset gesture, so clear it too.
        self._range = None
        self._selected = None
        self._active = None
        self._set_detail("")
        self.fit_to_events()
        return "break"

    def _on_motion(self, event: tk.Event) -> None:
        hit = self._hit_test(event.x, event.y)
        if hit is self._active:
            return
        self._active = hit
        if hit is None:
            self._hover_lbl.config(text="")
        else:
            when = format_timestamp(hit.timestamp) if hit.timestamp is not None else "undated"
            self._hover_lbl.config(text=f"{hit.entity} · {when}")
        self._redraw()

    def _on_leave(self, _event: tk.Event) -> None:
        if self._active is None:
            return
        self._active = None
        self._hover_lbl.config(text="")
        self._redraw()

    def _redraw_overlay_only(self) -> None:
        self._canvas.delete("overlay")
        _, _, top, bottom = self._plot_bounds()
        self._draw_selection_overlay(top, bottom)
        self._canvas.tag_lower("overlay")

    def _hit_test(self, x: int, y: int) -> TimelineEvent | None:
        if not self._dot_items:
            return None
        r = CLICK_TOLERANCE_PX
        candidates = self._canvas.find_overlapping(x - r, y - r, x + r, y + r)
        best: TimelineEvent | None = None
        best_d2 = r * r
        for item in candidates:
            index = self._dot_items.get(item)
            if index is None:
                continue
            coords = self._canvas.coords(item)
            if len(coords) != 4:
                continue
            cx = (coords[0] + coords[2]) / 2.0
            cy = (coords[1] + coords[3]) / 2.0
            d2 = (cx - x) ** 2 + (cy - y) ** 2
            if d2 <= best_d2:
                best_d2 = d2
                best = self._event_by_index(index)
        return best

    def _select_nearest(self, x: int, y: int) -> None:
        hit = self._hit_test(x, y)
        self._selected = hit
        self._set_detail(self._format_detail(hit) if hit is not None else "")

    def _event_by_index(self, index: int) -> TimelineEvent | None:
        for event in self._events:
            if event.index == index:
                return event
        return None

    # ------------------------------------------------------------- detail
    def _set_detail(self, text: str) -> None:
        self._text_adapter.set_text(self.detail, text)

    def _format_detail(self, event: TimelineEvent) -> str:
        when = (
            format_timestamp(event.timestamp)
            if event.timestamp is not None
            else "undated"
        )
        lines = [
            f"Entity:  {event.entity}",
            f"When:    {when}",
            f"Label:   {event.label}",
            "",
        ]
        try:
            lines.append(json.dumps(event.raw, indent=2, default=str, sort_keys=True))
        except (TypeError, ValueError):
            lines.append(repr(event.raw))
        return "\n".join(lines)


__all__ = [
    "ENTITY_FIELD_RANKING",
    "EntityTimeline",
    "LABEL_FIELD_RANKING",
    "TIMESTAMP_FIELD_RANKING",
    "TimelineEvent",
    "TextAdapter",
    "detect_entity",
    "detect_label",
    "detect_timestamp",
    "format_timestamp",
    "normalize_events",
    "parse_timestamp",
]
