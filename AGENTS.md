# Agent Guide — Egregore Native Coder Backend

## What this project is
Egregore is a sovereign, governed inference runtime. The Coder agent runs **directly inside the Egregore process** through the Egregore Model Service (EMS). There are no external inference engines, no proxies to third-party APIs, and no foreign model-server processes.

## Inference architecture
- **Backend name**: `egregore`
- **Proxy**: `src/egregore/ems/proxy.py` (`EmsProxy`) on `http://127.0.0.1:8001`
- **Lifecycle**: `src/egregore/ems/lifecycle.py` (`EmsLifecycle`) loads/unloads models in-process
- **Registry**: SQLite-backed catalog at `~/egregore_data/pioneer1/ems_registry.db`
- **Native inference engine**: `src/egregore/infrastructure/coder_backend.py` (`CoderBackend`)
- **Active Coder model**: `coder-ft-v2`
- **HF checkpoint**: `/mnt/blackstar/egregor_inventory/my_coder_fixed_hf`
- **Tokenizer chat template**: DeepSeek-Coder format with `### Instruction:` / `### Response:` / `<|EOT|>`. The EMS proxy enforces this format and injects a project-aware system prompt for `coder-ft-*` models.
- **Entry point**: `POST http://127.0.0.1:8001/v1/chat/completions`

## Required environment variables
Set these in `.env` or export before starting:

```bash
EGREGORE_CHAT_MODEL=coder-ft-v2
EGREGORE_DEFAULT_BACKEND=egregore
EGREGORE_EMS_URL=http://127.0.0.1:8001
EGREGORE_EMS_PROXY_PORT=8001
EGREGORE_EMS_DB=~/egregore_data/pioneer1/ems_registry.db
EGREGORE_CODER_MODEL_PATH=/mnt/blackstar/egregor_inventory/my_coder_fixed_hf
EGREGORE_MODEL_ROOT=/opt/egregore/models
EGREGORE_NODE_ID=pioneer1
```

Optional:

```bash
# Disable the native backend entirely (Egregore will start without it)
EGREGORE_CODER_BACKEND_ENABLED=false

# Run a minimal generation at startup to verify the pipeline
EGREGORE_CODER_WARMUP=true
```

## How to start the services

### Dashboard / Core API stack (current deployment)

The live stack runs as systemd **user** services. The units expect the repo to be checked out at `~/egregore` (the canonical name); `~/blackstar` is kept as a local compatibility symlink.

```bash
systemctl --user start egregore-core-api       # Core API on http://0.0.0.0:8002
systemctl --user start egregore-control-center # Control Center on http://0.0.0.0:3001
systemctl --user start egregore-gateway        # Dashboard gateway on http://0.0.0.0:3000
systemctl --user start egregore-bootstrap      # Projection Plane HTTPS API on https://0.0.0.0:8443
systemctl --user start egregore-ems-proxy      # EMS inference proxy on http://0.0.0.0:8001
systemctl --user start egregore-federation-watcher # Pioneer 2 handshake watcher
```

Deploy to a new node by copying the units from `deploy/systemd/user/` to `~/.config/systemd/user/` on the target host:

```bash
rsync -av deploy/systemd/user/ aiops@pioneer-2:.config/systemd/user/
ssh aiops@pioneer-2 'systemctl --user daemon-reload && systemctl --user enable egregore-core-api egregore-control-center egregore-gateway egregore-bootstrap egregore-federation-watcher'
```

Dashboard: `http://localhost:3000`

### EMS / native coder backend

Start the EMS proxy:

```bash
egregor proxy
```

Or via systemd:

```bash
sudo systemctl start egregore-ems-proxy
```

Load a model into the Egregore process:

```bash
egregor model serve coder-ft-v2
```

Or via systemd:

```bash
sudo systemctl start egregore-model-server@coder-ft-v2
```

Start the main Egregore server (legacy TLS entrypoint):

```bash
./start_server.sh
```

## How to verify inference

```bash
# List models
curl -s http://127.0.0.1:8001/v1/models

# Chat completion through Egregore
curl -s -X POST \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8001/v1/chat/completions \
  -d '{
    "model": "coder-ft-v2",
    "messages": [{"role": "user", "content": "Write a FastAPI endpoint that lists documents from anchorum.db and uses require_auth"}],
    "max_tokens": 512
  }'
```

Expected result: JSON response with generated FastAPI/Python code that imports project libraries (`anchorum.db`, `require_auth`) and follows the codebase conventions. The response will show `"model": "coder-ft-v2"`.

## Registering a model

```bash
egregor model register \
  coder-ft-v2 \
  /mnt/blackstar/egregor_inventory/my_coder_fixed_hf \
  --tier expert --ctx 16384 --chat-template deepseek

egregor model serve coder-ft-v2
```

## Installing dependencies

The native backend dependencies are declared as an optional extra in `pyproject.toml`:

```bash
pip install -e ".[llm-native]"
```

The exact versions validated in the deployment venv are also listed at the end of `requirements.txt`.

## Federation / cluster peers

Cluster node addresses are configured in `.env` via `EGREGORE_CLUSTER_NODES`:

```bash
# Use the bootstrap API (8443) for LAN peers, or the EMS proxy (8001) for WireGuard peers.
EGREGORE_CLUSTER_NODES=pioneer1=10.200.200.1:8001,pioneer2=10.200.200.2:8001,pioneer3=192.168.2.133:8443
PIONEER2_HOST=10.200.200.2
PIONEER2_PORT=8001
EGREGORE_PORT=8443
EGREGORE_NODE_ID=pioneer1
PEER_NODE_ID=pioneer2
EGREGORE_CONSTITUTION_PATH=config/egregore_constitution.yaml

# When peers are reached via the EMS proxy (port 8001), expose federation endpoints there too.
EGREGORE_EMS_PROXY_HOST=0.0.0.0
EGREGORE_MOUNT_FEDERATION_ON_EMS=true
EGREGORE_SCHEME=http           # peer scheme
EGREGORE_LOCAL_SCHEME=http     # local scheme
EGREGORE_LOCAL_PORT=8001       # local federation port
```

Federation endpoints (`/api/v1/federation/*`) are served by the bootstrap API on port `8443` by default. When `EGREGORE_MOUNT_FEDERATION_ON_EMS=true`, the EMS proxy on port `8001` also serves them, which is useful for WireGuard-only peers. The `egregore-federation-watcher` service polls Pioneer 2 and automatically proposes/ratifies a treaty when the peer comes online.

Check node health:

```bash
curl -s -k -H "X-API-Key: $(cat secrets/api_key.hex)" https://127.0.0.1:8443/health/nodes
```

Verify the EMS proxy exposes federation locally:

```bash
curl -s http://127.0.0.1:8001/api/v1/federation/treaty/active
curl -s http://127.0.0.1:8001/api/v1/federation/entropy
```

If the handshake verification loop reports `Peer did not report an active treaty`, the peer may return a list envelope (`{"active_treaties": [...]}`). The watcher in `scripts/federation_handshake_watcher.py` handles both single-object and list-envelope responses.

## Important constraints

- **Disk**: The HF model directory is ~13 GB on disk. Keep it on `/mnt/blackstar/vol-hdd-a` (has space), not root.
- **GPU**: The Coder model requires a CUDA GPU with at least ~8 GB free VRAM for 8-bit in-process loading.
- **Startup time**: Model load from HDD can take ~20–60 seconds; the EMS health timeout defaults to 300 s.
- **Streaming**: Token-by-token streaming is not yet implemented in `CoderBackend`; the full response is returned as one chunk.
- **No external APIs**: Do not add Ollama, OpenAI, or other external client wrappers. New models are added as native Egregore backends.

## What to check if inference breaks

1. Is `coder-ft-v2` listed by `GET http://127.0.0.1:8001/v1/models`?
2. Is the model loaded? `egregor model status`
3. Is the process using GPU memory? `nvidia-smi` should show the Egregore process owning ~8 GB.
4. Does the checkpoint exist? `ls -d "$EGREGORE_CODER_MODEL_PATH"`
5. Check logs for `CoderBackend` and `EmsProxy` errors.

## Files that matter for this backend

- `src/egregore/ems/proxy.py`
- `src/egregore/ems/lifecycle.py`
- `src/egregore/ems/registry.py`
- `src/egregore/ems/cli.py`
- `src/egregore/ems/prompts.py`
- `src/egregore/infrastructure/coder_backend.py`
- `src/egregore/infrastructure/egregore_llm_client.py`
- `deploy/systemd/egregore-ems-proxy.service`
- `deploy/systemd/egregore-model-server@.service`
- `start_server.sh`

## Factory telemetry (Phase 1 measurement)

Every factory run (`POST /api/v1/factory*`) emits canonical-JSONL telemetry via
`src/egregore/factory/telemetry.py`. Hook points: endpoint entry
(`factory.envelope.in`), each station (`factory.station`), each LLM call
(`factory.inference`, with unflattened usage + m1–m4 + inference_id), and
pipeline exit (`factory.run.outcome`). All events in one run share a `run_id`;
envelope runs also carry `task_id` / `task_fingerprint`.

Environment:

```bash
EGREGORE_FACTORY_TELEMETRY_DIR=report/factory_telemetry   # default
EGREGORE_FACTORY_TELEMETRY=off                             # disable entirely
```

Files: one `factory_YYYY-MM-DD.jsonl` per UTC day in the telemetry dir.

Histogram bucketer (trivial / micro_solvable / structured_final / heavy):

```bash
.venv/bin/python scripts/factory_histogram.py            # writes report/factory_histogram.json
.venv/bin/python scripts/factory_histogram.py --diff week1.json week2.json
```

## Factory QC gate (Station 5, fail-closed)

Every factory run passes a terminal-output QC gate before shipping
(`src/egregore/factory/qc_gate.py`). INVARIANT: the gate is **fail-closed** —
any error, timeout, malformed verdict, or low confidence is a FAIL, and
BLOCKED runs ship nothing (`final_output` withheld, M4 DIVERGED emitted).
Telemetry (`factory/telemetry.py`) is deliberately fail-OPEN — the opposite
failure mode. Do not harmonize the two.

Two tiers: deterministic checks (empty/oversize/forbidden patterns/m1–m4/
required fields), then a semantic critic (`CriticService` port, currently the
Egregore backend via `critic_model` config — a dedicated resident 1.5B is
Phase 6). Rework budget 2 with typed violations injected into the rework
prompt (never prose), then one heavy escalation pass, then BLOCKED.

Config: `config/factory_policy.json` (`qc` block). Kill switch:
`EGREGORE_FACTORY_QC=off` — bypassed runs still ship BUT emit a `QC_BYPASSED`
governance record and a `tier: bypassed` telemetry verdict. The bypass is on
the record by design.

## Factory policy governance (Phase 3)

`config/factory_policy.json` is the governance contract, loaded by
`src/egregore/factory/policy.py`. Rules:

- **Malformed policy = BLOCKED** — no station runs, nothing ships (fail-closed).
- **Precedence: env var > policy file > code defaults.** Env overrides
  (`EGREGORE_FACTORY_QC_REWORK_BUDGET`, `_CONFIDENCE_THRESHOLD`,
  `_CRITIC_TIMEOUT_MS`, `_CRITIC_MAX_TOKENS`, `_CRITIC_MODEL`) emit
  `factory.policy.override` telemetry records.
- **`policy_hash`** (SHA-256 of canonical merged policy) is embedded in
  `factory.envelope.in` and every `factory.run.outcome` — slice histograms by
  regime with it.
- **Hot reload** by mtime check at each run start (no watcher daemon).
- `EGREGORE_FACTORY_POLICY` overrides the policy file path (tests).

## Agent standards

When helping with self-study, curriculum design, or technical learning, all agents must follow the **elite self-study standard** defined in:

- `.agents/skills/elite_self_study/SKILL.md`
- `.agents/prompts/elite_self_study_standard.md`

Key points: modern textbook spine, problem-solving as the main activity, verified solutions, external feedback, spaced repetition, and honest competence ceilings.

### Primary-source enrichment library

After a chapter is mastered, agents should suggest a paired original source from:

- `.library/primary_pairings_physics_halliday_resnick_walker.md`
- `.library/primary_pairings_biology_campbell.md`
- `.library/primary_pairings_chemistry_mcmurry.md`

Use `.library/lookup_pairing.py` to query pairings programmatically:

```bash
python .library/lookup_pairing.py --textbook physics_halliday_resnick_walker --chapter 13
python .library/lookup_pairing.py --textbook biology_campbell --topic "DNA structure"
python .library/lookup_pairing.py --textbook chemistry_mcmurry --chapter 13
python .library/lookup_pairing.py --list
```
