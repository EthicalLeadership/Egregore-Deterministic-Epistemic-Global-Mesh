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

Start the main Egregore server (if needed):

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
