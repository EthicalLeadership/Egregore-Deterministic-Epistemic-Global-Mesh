"""Widget-level tests for EntityTimeline.

These exercise real Tk widgets — fit-state handling, selection-overlay draw
order, and text-adapter wiring. They skip themselves when no display is
available, matching the repo's headless-CI convention.
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from entity_timeline import EntityTimeline  # noqa: E402

BASE = 1_700_000_000.0


def _display_available() -> bool:
    try:
        root = tk.Tk()
    except Exception:
        return False
    root.destroy()
    return True


pytestmark = pytest.mark.skipif(
    not _display_available(), reason="Tk display not available"
)


@pytest.fixture()
def root():
    window = tk.Tk()
    window.withdraw()
    yield window
    window.destroy()


def _events(count: int) -> list[dict[str, Any]]:
    return [
        {"entity": f"e{i % 3}", "timestamp": BASE + i * 60, "summary": f"event {i}"}
        for i in range(count)
    ]


class _SpyAdapter:
    """Records which text-adapter hooks the widget calls."""

    calls: list[str] = []

    @staticmethod
    def make_readonly_copyable(widget: tk.Text) -> tk.Text:
        _SpyAdapter.calls.append("make_readonly_copyable")
        return widget

    @staticmethod
    def install_context_menu(widget: tk.Widget) -> None:
        _SpyAdapter.calls.append("install_context_menu")

    @staticmethod
    def set_text(widget: tk.Text, text: str) -> None:
        _SpyAdapter.calls.append("set_text")
        widget.delete("1.0", tk.END)
        if text:
            widget.insert(tk.END, text)


# ------------------------------------------------------------- fit behaviour
def test_initial_load_fits_the_view(root: tk.Tk) -> None:
    view = EntityTimeline(root)
    view.set_events(_events(5))
    assert view._view is not None
    assert view._has_fitted is True


def test_refresh_does_not_refit(root: tk.Tk) -> None:
    view = EntityTimeline(root)
    view.set_events(_events(5))
    fitted = view._view

    view.set_events(_events(40))
    assert view._view == fitted  # refresh keeps the operator's viewport
    assert len(view.events()) == 40


def test_explicit_fit_refits_to_new_data(root: tk.Tk) -> None:
    view = EntityTimeline(root)
    view.set_events(_events(5))
    view.set_events(_events(40))
    stale = view._view

    view.fit_to_events()

    assert view._view != stale
    stamps = [event.timestamp for event in view.events() if event.timestamp]
    assert view._view[0] <= min(stamps)
    assert view._view[1] >= max(stamps)


def test_double_click_refits_and_consumes_the_event(root: tk.Tk) -> None:
    view = EntityTimeline(root)
    view.set_events(_events(5))
    view.set_events(_events(40), fit=False)
    stale = view._view

    assert view._on_double_click(None) == "break"
    assert view._view != stale


def test_fit_argument_overrides_the_automatic_behaviour(root: tk.Tk) -> None:
    view = EntityTimeline(root)
    view.set_events(_events(5), fit=False)
    assert view._view is None

    view.set_events(_events(5), fit=True)
    assert view._view is not None


def test_empty_first_load_does_not_consume_the_initial_fit(root: tk.Tk) -> None:
    view = EntityTimeline(root)
    view.set_events([])
    assert view._has_fitted is False
    assert view._view is None

    view.set_events(_events(5))
    assert view._view is not None  # first *data* load still fits


def test_clear_resets_fit_state(root: tk.Tk) -> None:
    view = EntityTimeline(root)
    view.set_events(_events(5))
    view.clear()

    assert view._view is None
    assert view._has_fitted is False
    assert view.events() == []

    view.set_events(_events(5))
    assert view._view is not None


# --------------------------------------------------------------- draw order
def test_selection_overlay_renders_beneath_the_event_dots(root: tk.Tk) -> None:
    view = EntityTimeline(root)
    view.set_events(_events(6))
    view._range = (BASE, BASE + 120)
    view._redraw()

    order = list(view._canvas.find_all())
    overlay = set(view._canvas.find_withtag("overlay"))
    dots = set(view._canvas.find_withtag("dot"))

    assert overlay, "expected overlay items when a range is set"
    assert dots, "expected event dots"
    last_overlay = max(order.index(item) for item in overlay)
    first_dot = min(order.index(item) for item in dots)
    assert last_overlay < first_dot


def test_empty_timeline_renders_a_placeholder(root: tk.Tk) -> None:
    view = EntityTimeline(root)
    view.set_events([])

    labels = [
        view._canvas.itemcget(item, "text")
        for item in view._canvas.find_all()
        if view._canvas.type(item) == "text"
    ]
    assert any("No timeline events" in label for label in labels)


# ------------------------------------------------------------------ adapter
def test_detail_pane_uses_the_injected_text_adapter(root: tk.Tk) -> None:
    _SpyAdapter.calls = []
    view = EntityTimeline(root, text_adapter=_SpyAdapter)

    assert "make_readonly_copyable" in _SpyAdapter.calls
    assert "install_context_menu" in _SpyAdapter.calls

    view.set_events(_events(3))
    view._set_detail("hello")
    assert _SpyAdapter.calls[-1] == "set_text"
    assert "hello" in view.detail.get("1.0", tk.END)


def test_module_carries_no_messagebox_import() -> None:
    source = (Path(__file__).resolve().parents[1] / "entity_timeline.py").read_text(
        encoding="utf-8"
    )
    assert "messagebox" not in source


def test_overlay_is_clamped_when_range_exceeds_the_view(root: tk.Tk) -> None:
    """Regression: out-of-view ranges must not draw overlay inside the plot."""
    view = EntityTimeline(root)
    view.set_events([
        {"entity": "a", "timestamp": BASE + i, "summary": f"e{i}"}
        for i in range(6)
    ])
    view._range = (BASE - 10_000, BASE + 10_000)
    view._redraw()

    left, right, top, bottom = view._plot_bounds()
    for item in view._canvas.find_withtag("overlay"):
        x0, y0, x1, y1 = view._canvas.coords(item)
        assert left - 0.5 <= x0 <= x1 <= right + 0.5
        assert top - 0.5 <= y0 <= y1 <= bottom + 0.5


def test_hit_test_picks_the_nearest_of_two_close_dots(root: tk.Tk) -> None:
    """Regression: hit-test must find dots via find_overlapping, nearest wins."""
    view = EntityTimeline(root)
    view.set_events([
        {"entity": "a", "timestamp": BASE + 1, "summary": "left"},
        {"entity": "a", "timestamp": BASE + 3, "summary": "right"},
    ])
    first = view.events()[0]
    coords = None
    for item, index in view._dot_items.items():
        if index == first.index:
            coords = view._canvas.coords(item)
            break
    assert coords is not None
    cx = (coords[0] + coords[2]) / 2.0
    cy = (coords[1] + coords[3]) / 2.0
    hit = view._hit_test(int(cx), int(cy))
    assert hit is not None and hit.index == first.index


def test_lane_merging_is_disclosed(root: tk.Tk) -> None:
    view = EntityTimeline(root)
    view.set_events([{"entity": f"e{i}", "timestamp": BASE + i, "summary": f"e{i}"} for i in range(20)])
    assert "lanes merged" in view._count_lbl.cget("text")


def test_undated_events_are_stacked_not_overlapped(root: tk.Tk) -> None:
    view = EntityTimeline(root)
    view.set_events([{"entity": "a", "summary": f"undated {i}"} for i in range(4)])
    tops = {round(view._canvas.coords(item)[1]) for item in view._dot_items}
    assert len(tops) == 4


def test_all_undated_load_resets_view(root: tk.Tk) -> None:
    view = EntityTimeline(root)
    view.set_events([{"entity": "a", "timestamp": BASE + i, "summary": f"e{i}"} for i in range(5)])
    assert view._view is not None
    view.set_events([{"entity": "a", "summary": "no date"} for _ in range(3)])
    assert view._view is None
    assert view._has_fitted is False
    view.set_events([{"entity": "a", "timestamp": BASE + i} for i in range(5)])
    assert view._view is not None


def test_double_click_refits_and_clears_selection(root: tk.Tk) -> None:
    """Double-click is a reset gesture: refit, clear selection, clear range."""
    view = EntityTimeline(root)
    view.set_events([{"entity": "a", "timestamp": BASE + i, "summary": f"e{i}"} for i in range(5)])
    view._selected = view.events()[0]
    view._range = (BASE, BASE + 2)
    assert view._on_double_click(None) == "break"
    assert view._view is not None
    assert view._selected is None
    assert view._range is None
