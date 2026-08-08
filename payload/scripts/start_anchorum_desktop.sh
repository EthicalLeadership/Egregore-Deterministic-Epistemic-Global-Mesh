#!/bin/bash
# Launch the ANCHORUM native desktop app.
cd /home/kark/blackstar || exit 1
export DISPLAY="${DISPLAY:-:0}"
exec /home/kark/blackstar/.venv/bin/python anchorum_desktop.py
