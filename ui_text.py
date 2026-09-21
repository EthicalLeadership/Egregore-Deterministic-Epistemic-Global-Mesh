#!/usr/bin/env python3
"""Copy/paste machinery for the ANCHORUM desktop app's text widgets.

Tk's ``state=tk.DISABLED`` makes a Text widget unselectable, which kills
copy. The widgets here stay ``NORMAL`` (selection, Ctrl+C, and keyboard
navigation all work natively) while every mutation path is blocked, so they
behave as read-only for the user and stay writable for the app.

Nothing in this module requires a display at import time.
"""

from __future__ import annotations

import tkinter as tk

_EDITING_KEYSYMS = frozenset({"BackSpace", "Delete", "Return", "Tab", "KP_Enter"})
_CTRL_OR_ALT = 0x0004 | 0x0008  # ControlMask | Mod1Mask (Alt)

# Marker attribute set on widgets passed through make_readonly_copyable so the
# context menu knows to hide Cut/Paste.
_READONLY_MARK = "_anchorum_readonly"


def make_readonly_copyable(w: tk.Text) -> tk.Text:
    """Make a Text widget read-only for the user but fully selectable/copyable."""

    def _block_edit(event: tk.Event) -> str | None:
        if event.keysym in _EDITING_KEYSYMS:
            return "break"
        # Block printable typing; let Ctrl/Alt combos through (Ctrl+C copy,
        # Ctrl+A select-all; navigation keys have no char and pass anyway).
        if getattr(event, "char", "") and not (event.state & _CTRL_OR_ALT):
            return "break"
        return None

    w.bind("<Key>", _block_edit)
    for virtual in ("<<Cut>>", "<<Paste>>", "<<PasteSelection>>"):
        w.bind(virtual, lambda _e: "break")
    w.bind("<Button-2>", lambda _e: "break")  # X11 middle-click paste
    w.bind("<Control-a>", lambda _e: _select_all(w))
    setattr(w, _READONLY_MARK, True)
    return w


def _select_all(w: tk.Text) -> str:
    w.tag_add("sel", "1.0", "end-1c")
    w.mark_set("insert", "1.0")
    return "break"


def set_text(w: tk.Text, text: str) -> None:
    """Replace the contents of a copyable-read-only Text widget."""
    w.delete("1.0", tk.END)
    if text:
        w.insert(tk.END, text)


def append_text(w: tk.Text, text: str, tag: str | None = None) -> None:
    """Append to a copyable-read-only Text widget and scroll to the end."""
    if tag is None:
        w.insert(tk.END, text)
    else:
        w.insert(tk.END, text, tag)
    w.see(tk.END)


def install_context_menu(w: tk.Widget) -> tk.Menu:
    """Right-click Cut/Copy/Paste/Select-All menu for Text and Entry widgets.

    Text widgets flagged by :func:`make_readonly_copyable` only get Copy and
    Select All — the editing entries would be blocked anyway.
    """
    readonly = isinstance(w, tk.Text) and bool(getattr(w, _READONLY_MARK, False))
    menu = tk.Menu(w, tearoff=0)

    def _virtual(event: str) -> None:
        w.event_generate(event)

    if not readonly:
        menu.add_command(label="Cut", command=lambda: _virtual("<<Cut>>"))
    menu.add_command(label="Copy", command=lambda: _virtual("<<Copy>>"))
    if not readonly:
        menu.add_command(label="Paste", command=lambda: _virtual("<<Paste>>"))
    menu.add_separator()
    if isinstance(w, tk.Text):
        menu.add_command(label="Select All", command=lambda: _select_all(w))
    else:
        menu.add_command(
            label="Select All",
            command=lambda: (w.selection_range(0, tk.END), w.icursor(tk.END)),
        )

    def _popup(event: tk.Event) -> None:
        menu.tk_popup(event.x_root, event.y_root)

    w.bind("<Button-3>", _popup)
    return menu
