python - <<'PYEOF'
from pathlib import Path

text = """# Mission Job Bridge

This document describes the bridge between the chat interpreter and the Egregore job scheduler / work tree pipeline.

---

## 1. Overview

The system now supports:

- `/mission <intent>` — submit a mission through the job pipeline.
- `/nodes` — list active compute nodes.
- `/status` — show scheduler queue depth and node count.

The bridge uses the existing `WorkTreeService`, `NodeRegistry`, `NodeSelector`, and `JobScheduler`. It does **not** replace the scheduler or rebuild the work tree system.

---

## 2. Architecture
