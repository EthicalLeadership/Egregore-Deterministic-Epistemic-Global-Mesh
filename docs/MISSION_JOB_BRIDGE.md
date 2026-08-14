# Mission Job Bridge

This document describes the bridge between the chat interpreter and the Egregore job scheduler / work tree pipeline.

---

## 1. Overview

The system now supports:

- `/mission <intent>` — submit a mission through the job pipeline.
- `/nodes` — list active compute nodes.
- `/status` — show scheduler queue depth and node count.

The bridge uses the existing `WorkTreeService`, `NodeRegistry`, `NodeSelector`, and `JobScheduler`. It does **not** replace the scheduler or rebuild the work tree system.

**Important:** these commands only work on the server that wires `app.state.job_runtime` — the **projection-plane bootstrap** app (`egregore.interface.bootstrap`, port **8443**). The plain `http_api` app (port 8002) does *not* wire the job runtime and returns `"Job runtime is not configured."` for these commands. See §6.

---

## 2. Architecture

```
Chat command /mission
        ↓
ChatContext.env["job_runtime"]
        ↓
WorkTreeService.submit_tree(root_work_unit, timestamp_ns)
        ↓
decompose via IWorkDecomposition (default NoOpWorkDecomposition)
        ↓
dispatch leaves via _Scheduler
        ↓
JobRouterSchedulerAdapter.submit(work_unit, timestamp_ns)
        ↓
WorkUnit → JobRequest → DefaultJobClassifier → JobClassification
        ↓
Job → NodeSelector.route → RoutingDecision
        ↓
Job → JobScheduler.submit → SQLiteJobStore
```

---

## 3. Key Components

| Component | File | Purpose |
|---|---|---|
| `JobRouterSchedulerAdapter` | `application/job_router_scheduler_adapter.py` | Implements `WorkTreeService._Scheduler` |
| `DefaultJobClassifier` | `application/default_job_classifier.py` | Deterministic `IJobClassifier` |
| `NoOpWorkDecomposition` | `application/default_work_decomposition.py` | Leaf-only `IWorkDecomposition` |
| `JobRuntime` | `application/job_runtime.py` | Factory assembling the job pipeline |
| `SQLiteJobStore` | `infrastructure/persistence/sqlite_job_store.py` | Persistent job queue |
| `SQLiteWorkTreeStore` | `infrastructure/persistence/sqlite_work_tree_store.py` | Persistent work trees |

The `job_runtime` is built by `build_job_runtime()` in `application/job_runtime.py`, which loads every node definition from `config/nodes/*.json` into an `InMemoryNodeStore` and assembles the full pipeline.

---

## 4. Persistence

Jobs and work trees are stored in the same SQLite database used by users and dossiers:

- `node.db` in `EGREGORE_DATA_DIR` or `~/egregore_data/<node_id>` (default node id: `pioneer1`)

Tables:

- `jobs` — scheduler queue.
- `work_trees` — work tree snapshots.

Migrations (see `infrastructure/persistence/migrate.py`):

- Migration 1: `schema_migrations`, `case_versions`, `dossier_commits`
- Migration 2: `accounts`, `users`, `vertical_grants`, `invites`, `api_keys`
- Migration 3: `user_passwords`
- Migration 4: `jobs`, `work_trees`

Both SQLite stores auto-run `SQLITE_MIGRATIONS` up to the latest version on first open (`_ensure_migrated`).

---

## 5. Provisioning

### Accounts

```bash
egregore-admin account create <account-name> <admin-username>
```

Prompts for password twice and stores an scrypt hash in `user_passwords`.

### Nodes

```bash
egregore-admin node register <node-id> \
  --capabilities llm gpu \
  --cpu-percent 50.0 \
  --memory-mb 32768 \
  --vram-mb 12288 \
  --disk-iops 1000 \
  --network-mbps 1000
```

Node definitions are written to `config/nodes/<node-id>.json` and loaded by `build_job_runtime()`.

List nodes:

```bash
egregore-admin node list
```

---

## 6. Services

| Service | Unit (user-level) | Port | Wires `job_runtime`? |
|---|---|---|---|
| Projection-plane bootstrap (job runtime, chat WS) | `egregore-bootstrap` | 8443 (TLS) | **Yes** |
| Core Python API (plain HTTP) | `egregore-core-api` | 8002 | No |
| ANCHORUM HTTP | `egregore-anchorum-http` | 8080 | No |
| Control Center | `egregore-control-center` | 3001 | No |
| Dashboard Gateway | `egregore-gateway` | 3000 | No |
| EMS proxy | `egregore-ems-proxy` | 8001 | No |
| Federation watcher | `egregore-federation-watcher` | n/a (client) | No |

Notes:

- The **only** service with an active job runtime is `egregore-bootstrap` (`egregore.interface.bootstrap:create_app --factory --port 8443`, TLS using `certs/dashboard.key`/`certs/dashboard.crt`). Its `create_app()` sets `app.state.job_runtime = build_job_runtime()`, which `ws_chat.py` reads into `ChatContext.env["job_runtime"]`.
- There is **no** `egregore-main` service and nothing binds port **8000**. The federation watcher script defaults its local API port to **8443** (`EGREGORE_PORT`/`EGREGORE_LOCAL_PORT`), matching the bootstrap plane.
- The plain HTTP API (`egregore-core-api`, `http_api/http/app.py`) attaches only `inference_service`, `code_factory`, and `container` to `app.state` — `/mission`, `/nodes`, and `/status` report "Job runtime is not configured." there.
- `start_server.sh` and `scripts/run_chat_server.sh` both launch the bootstrap app on 8443.

---

## 7. Rollback

The legacy archive `_legacy_archive_20260814_011422` (HDD) and `/media/kark/7A7666CA4E34EADC2/egregore_legacy_full_20260814_011422` (USB) are **partial archives**: they contain only the module set recorded in `_legacy_archive_20260814_011422/manifest.json` (e.g. `egregore.version`, `egregore.http_api`, `egregore.powertrain.load_regulator`, DOSSER cell modules).

**Do not blanket-restore** with `cp -a …/src/egregore/* src/egregore/` — the archive holds a *subset* of the current tree, so that would silently overwrite unrelated live modules that share names (e.g. `version.py`, `http_api/`).

Restore *specific modules only*, one at a time, after confirming the target is missing/broken:

```bash
# Example: restore a single archived module (dotted path → file/dir under src/egregore)
cp -a _legacy_archive_20260814_011422/src/egregore/version.py src/egregore/version.py
cp -a _legacy_archive_20260814_011422/src/egregore/http_api src/egregore/http_api
```

List every archived module path before restoring:

```bash
python3 -c "import json;print('\n'.join(json.load(open('_legacy_archive_20260814_011422/manifest.json'))['copied_modules']))"
```

Verify archive integrity with:

```bash
scripts/verify_archives.sh
```

The verify script reports whether each manifest-listed module exists in both archives, and diffs the two `src/egregore` trees when the USB archive is mounted. Note: the archives do **not** contain `SHA256SUMS` files, so verification is manifest + tree-diff based.

---

## 8. Verification

Because the job runtime is only wired on the TLS bootstrap plane, the WebSocket smoke test targets **wss://localhost:8443**.

```bash
python - <<'PYEOF'
import asyncio, json
from websockets import connect

API_KEY = "YOUR_API_KEY"  # must be a valid key (e.g. from secrets/api_key.hex / .env EGREGORE_API_KEYS)
URI = f"wss://localhost:8443/ws/chat/test-session?api_key={API_KEY}"

async def main():
    async with connect(URI) as ws:
        for cmd in ["/nodes", "/status", "/mission smoke test"]:
            await ws.send(cmd)
            raw = await ws.recv()
            print(raw)
            print("---")

asyncio.run(main())
PYEOF
```

Notes:

- The server requires a valid API key; unauthenticated connections are closed with code **1008** ("Authentication required").
- Responses are canonical JSON envelopes, not bare strings. Expected:
  - `/nodes` → `"summary": "Found 2 ACTIVE node(s)."` with `detail.nodes` listing `pioneer1` and `pioneer2`.
  - `/status` → `"summary": "Queue depth: N job(s). Active nodes: 2."`
  - `/mission smoke test` → `"ok": true`, `"summary": "Mission submitted. Final state: DISPATCHED"`, and `detail.mission_id` / `detail.final_state` — the dispatcher state (`DISPATCHED`) appears inside the envelope, not as a bare reply.
- If the mission fails routing (e.g. no ACTIVE node matches the requested capabilities), the final state is `REJECTED`.
