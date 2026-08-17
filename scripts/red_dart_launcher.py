#!/usr/bin/env python3
"""Launcher wrapper for the Red Dart CLI installed in the Egregore venv."""

from __future__ import annotations

from red_dart.main import main

if __name__ == "__main__":
    raise SystemExit(main())
