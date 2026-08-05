# Getting Started — Egregore

Clone to a passing smoke test in about 10 minutes. This guide covers the
local developer workflow (npm-first, project-managed `.venv`). For production
deployment see `DEPLOYMENT.md`; for the live systemd stack see `AGENTS.md`.

## Prerequisites

- Linux (x86_64), Python 3.11 or 3.12
- Node.js 20+ and npm
- Postgres, Redis, and NATS running locally (see step 3)
- ~2 GB free disk for the Python environment and frontend build

## 1. Clone

```bash
git clone <repo-url> egregore
cd egregore
```

## 2. Install everything

```bash
npm install
```

`npm install` triggers `postinstall`, which runs both:

- `install:frontend` — `cd frontend && npm install`
- `install:python` — `bash scripts/install_python.sh` (creates `.venv` and
  installs the Python package with the messaging/persistence/telemetry extras)

If you prefer to do it manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[messaging,persistence,telemetry]"
cd frontend && npm install && cd ..
```

## 3. Configure and check services

Create a `.env` in the repo root with the variables required for your
environment (see `DEPLOYMENT.md` for the full list). Minimum for local dev:

```bash
EGREGORE_DB_URL=postgresql://egregore:egregore@localhost:5432/egregore
REDIS_URL=redis://localhost:6379/0
NATS_URL=nats://localhost:4222
EGREGORE_NODE_ID=dev-node
```

Verify the backing services are reachable:

```bash
npm run services:check
```

## 4. Start the stack

```bash
npm run dev        # frontend gateway on http://localhost:3000
```

Start the core API (FastAPI on port 8002) in a second terminal:

```bash
source .venv/bin/activate
PYTHONPATH=src uvicorn egregore.http_api.http.app:create_app --factory --port 8002
```

## 5. Smoke test

With the gateway and core running:

```bash
npm run smoke      # scripts/smoke_test.sh
```

Expected output: `PASS` lines confirming

1. gateway `/health` returns 200,
2. core `/health` returns 200,
3. core `/ready` reports `db` / `redis` / `nats` all `ok`,
4. a workflow can be created and read back end-to-end.

## 6. Run the governance gates (optional but recommended)

```bash
npm run test:arch    # layer purity + closed import surfaces
npm test             # full test suite
```

These are the same gates CI runs (`.github/workflows/ci.yml`) before merge.

## Where to go next

| Goal | Document |
|------|----------|
| Production deploy (systemd / Docker) | `DEPLOYMENT.md` |
| Deployment gates and rollback | `docs/pipeline.md` |
| Incident response | `docs/runbook.md` |
| ANCHORUM forensic product | `ANCHORUM_QUICKSTART.md` |
| Inference backend (EMS / coder model) | `AGENTS.md` |
| What changed in each release | `CHANGELOG.md` |
