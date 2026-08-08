# Egregore Model Orchestration Determinism Audit

**Audit date:** 2026-06-29  
**Auditor:** Senior Forensic Code Auditor (automated)  
**Scope:** LLM inference determinism for the Egregore core running under CBI-0 governance, with GGUF models loaded via the "5-station factory" on Pioneer 1 or a remote server.  
**Assumption:** Same hardware, same model file (same SHA-256), same software version.

---

## 1. Executive Summary

**Determinism verdict: NO (PARTIAL at best)**

The Egregore inference stack is **not unconditionally deterministic** for the paths that actually exercise the native GGUF host (`EgregoreModelHost` / `LocalLlmAdapter`). The code contains deterministic *building blocks*—a fixed model-load seed of `42`, greedy sampling defaults (`top_p=1.0`, `top_k=1`) inside `LocalLlmAdapter`, and a deterministic `InferenceMode.DETERMINISTIC` concept—but these are **not wired together** on the main native-GGUF path.

The weakest links are:

1. **`ChatInferenceOrchestrator`** hard-codes `temperature=0.7` and never passes a seed to `EgregoreModelHost`.
2. **`EgregoreModelHost`** accepts whatever `request.temperature` it receives and forwards it to `LocalLlmAdapter`, with no deterministic override.
3. **`InferenceRequest`** (the canonical port) has **no `seed` field**, so no caller can request deterministic sampling through the model-host abstraction.
4. **`factory_router.py`** (the "5/7-station factory") loads `Llama()` without a seed, uses per-station temperatures of `0.1–0.6`, and never fixes `top_p`/`top_k` or disables request entropy.
5. The HTTP `/v1/chat/completions` endpoint defaults to `mode="deterministic"` and `seed=42`, but it routes through `InferenceService` to **Ollama/Anthropic/DeepSeek/local-HF** backends—not `EgregoreModelHost`—so it does not govern the native GGUF path at all.

Conclusion: **A forensic replay of the same prompt through `ChatInferenceOrchestrator` or the factory router is not guaranteed to be bit-identical.** A dedicated deterministic mode would need to be added and tested.

---

## 2. Detailed Findings per Code File

### 2.1 `src/egregore/infrastructure/egregore_model_host.py`

| Question | Finding |
|----------|---------|
| Temperature set? | Taken from `request.temperature` and passed unchanged to the adapter. |
| Seed set? | **No seed control.** The host relies on `LocalLlmAdapter`'s model-load seed (`42`). The per-generation RNG state is not reset. |
| Deterministic flags? | None. No `--deterministic`, `--no-mmap`, `--mlock`, `n_batch`, or `n_ubatch` controls. |
| Sampling method? | Delegated to adapter; defaults greedy inside adapter, but overridden by caller temperature. |
| Request entropy? | `request_id = str(uuid.uuid4())` is generated for every result, but this is **not fed into sampling**. It only affects the response envelope. |
| KV-cache / batch effects? | Adapters are cached by `{model_id}:{n_gpu_layers}:{n_threads}`. Persistent process state means the KV cache could retain context between calls if the caller reuses the same adapter instance; however, each `generate()`/`chat()` call appears to start from the supplied prompt/messages, not a previous KV state. |

**Key code:**
```python
result = adapter.generate(
    prompt=prompt,
    max_tokens=request.max_tokens,
    temperature=request.temperature,   # <- caller-controlled, no override
)
```

### 2.2 `src/egregore/infrastructure/local_llm_adapter.py`

| Question | Finding |
|----------|---------|
| Temperature set? | `generate()` and `chat()` default to `temperature=0.0`, but the caller can override. `EgregoreModelHost` overrides with `0.7`. |
| Seed set? | `seed=42` is passed to `Llama(...)` at **model-load time only** (`kwargs["seed"] = self.seed`). There is **no per-call seed reset**. |
| Deterministic flags? | `top_p=1.0`, `top_k=1` are hard-coded on every call (greedy / no nucleus). No `no_mmap`, `use_mlock`, `n_batch`, `n_ubatch`, or `deterministic` LlamaCpp flags. |
| Sampling method? | Greedy when temperature is `0.0`; with `temperature=0.7` the sampling is non-deterministic because the RNG is not re-seeded per call. |
| Request entropy? | None injected into generation. Prompts are hashed for provenance only. |
| KV-cache / batch effects? | The `Llama` instance is cached (`self._llm`). llama.cpp may use thread-parallel matrix ops; the Python wrapper does not set `n_threads` unless the caller provides it, and there is no env pinning. |

**Key code:**
```python
self._llm = Llama(
    model_path=self.model_path,
    n_ctx=self.n_ctx,
    seed=self.seed,          # <- load-time seed only
    verbose=False,
    n_gpu_layers=self.n_gpu_layers,
)

out = llm(
    prompt,
    max_tokens=max_tokens,
    temperature=temperature,  # <- overridden by caller to 0.7
    top_p=1.0,
    top_k=1,
    stop=stop,
)
```

### 2.3 `src/egregore/application/chat_inference_orchestrator.py`

| Question | Finding |
|----------|---------|
| Temperature set? | **Hard-coded to `0.7`** in both `ask()` and `chat()`. |
| Seed set? | **No seed.** `InferenceRequest` has no seed field. |
| Deterministic flags? | None. |
| Sampling method? | Non-deterministic by default (`temp=0.7`). |
| Request entropy? | None beyond normal request fields. |
| KV-cache / batch effects? | Uses `CapacityOrchestrator.schedule_inference()`, which is deterministic from hardware snapshot and model size. |

**Key code:**
```python
request = InferenceRequest(
    model_id=resolved_model_id,
    input_data=prompt.encode("utf-8"),
    max_tokens=256,
    temperature=0.7,          # <- NON-DETERMINISTIC
    backend="egregore",
    priority=100,
)
```

### 2.4 `src/egregore/interface/model_host_ports.py`

| Question | Finding |
|----------|---------|
| Temperature / seed? | `InferenceRequest` has `temperature: float = 0.7`. **There is no `seed` field.** The port cannot express deterministic sampling requirements. |

### 2.5 `src/egregore/application/capacity_orchestrator.py`

| Question | Finding |
|----------|---------|
| Entropy? | No randomness. Placement is derived from `hardware_snapshot()` and `decide_placement()`. |
| Scheduling effects? | Admission/epoch scheduling is deterministic given identical demand and identical epoch state. It does not affect token sampling. |

### 2.6 `src/egregore/application/inference_service.py`

| Question | Finding |
|----------|---------|
| Temperature / seed? | Routes `ChatRequest` (which has `mode` and `seed`) to the selected backend. M4 audit records `mode`, `max_tokens`, and `seed`, but **does not enforce** determinism. |
| Backend routing | Native GGUF (`EgregoreModelHost`) is **not registered** in the default `build_inference_service_from_env()` factory. Default is Ollama; prefixes route to Anthropic/DeepSeek/local-HF. |

### 2.7 `src/egregore/http_api/http/v1/chat.py`

| Question | Finding |
|----------|---------|
| Defaults | `mode="deterministic"`, `seed=42`, `max_tokens=2048`. |
| Determinism? | The *request* is deterministic, but it is dispatched through `InferenceService` to Ollama/Anthropic/DeepSeek/local. It **never reaches `EgregoreModelHost`**. Ollama's adapter correctly maps deterministic mode to `temperature=0.0`, `seed=42`, `top_p=1.0`, `top_k=1`. Anthropic/DeepSeek receive `temperature=0.0`/`top_p=1.0` but remote providers do not guarantee bit-identical output. |

### 2.8 `src/egregore/interface/factory_router.py` (5-station / 7-stage factory)

| Question | Finding |
|----------|---------|
| Seed set? | **No seed** passed to `Llama(...)`. |
| Temperature set? | Per-station config uses `0.1`, `0.2`, `0.3`, `0.6` (see `config/factory_profiles_v2.yaml`). Request override allowed. |
| top_p / top_k? | **Not set** in `_call_llm`. Whatever llama.cpp defaults to applies. |
| Deterministic flags? | None. |
| Request entropy? | `time.time_ns()` and `time.monotonic()` are used for provenance timing only, not sampling. |
| KV-cache? | Models cached in `ModelHost._cache`. Each `_call_llm` builds a fresh `messages` list, so no explicit context carry-over, but the cached `Llama` object retains internal state. |

### 2.9 Remote / non-GGUF backends

| Backend | Deterministic handling | Forensic reliability |
|---------|------------------------|----------------------|
| `OllamaClient` | Maps `InferenceMode.DETERMINISTIC` to `temperature=0.0`, `seed`, `top_p=1.0`, `top_k=1`. | Dependent on Ollama/llama.cpp version and server state; not bit-identical guaranteed across process restarts. |
| `LocalModelClient` (HF/transformers) | `do_sample=False`, `temperature=0.0`, `top_p=1.0`. | PyTorch may still have non-deterministic ops (e.g., `sparse`/`scatter_add`) unless `torch.use_deterministic_algorithms(True)` is set. It is **not** set in the code. |
| `AnthropicClient` / `DeepSeekClient` | Send `temperature=0.0`, `top_p=1.0`. | Remote API; no bit-identical guarantee, no seed parameter forwarded. |

---

## 3. Reproducibility Assessment (Step-by-Step Verification)

To empirically verify determinism on Pioneer 1:

1. **Prerequisites**
   - Identify or register a verified GGUF model in `${MODELS_DIR}/gguf/`.
   - Ensure the model file hash matches the catalog entry.
   - Pin environment: same `llama-cpp-python` version, same Python, same OS driver/CUDA/ROCm stack.

2. **Test A — Native GGUF host via `ChatInferenceOrchestrator`**
   ```python
   from egregore.application.chat_inference_orchestrator import ChatInferenceOrchestrator
   orch = ChatInferenceOrchestrator()
   r1 = orch.ask("What is 2+2? Answer with one word.")
   r2 = orch.ask("What is 2+2? Answer with one word.")
   assert r1.text == r2.text, "Non-deterministic!"
   ```
   **Expected:** Likely **FAILS** because `temperature=0.7` is used and no per-call seed is set.

3. **Test B — Direct `LocalLlmAdapter` with greedy settings**
   ```python
   from egregore.infrastructure.local_llm_adapter import LocalLlmAdapter
   adapter = LocalLlmAdapter("/path/to/model.gguf", seed=42, n_ctx=2048)
   o1 = adapter.generate(prompt="2+2=?", max_tokens=10, temperature=0.0)
   o2 = adapter.generate(prompt="2+2=?", max_tokens=10, temperature=0.0)
   assert o1["text"] == o2["text"]
   assert o1["output_hash"] == o2["output_hash"]
   ```
   **Expected:** Likely **PASSES** on CPU; on GPU, llama.cpp may still exhibit non-determinism from cuBLAS/hipBLAS unless `n_gpu_layers=0` or deterministic env flags are set.

4. **Test C — Factory router twice**
   ```bash
   curl -s -X POST http://localhost:8000/api/v1/factory \
     -H "X-API-Key: $KEY" \
     -H "Content-Type: application/json" \
     -d '{"input":"write a hello world function"}' > run1.json
   # restart server or wait, repeat
   diff run1.json run2.json
   ```
   **Expected:** Likely **DIFFERS** because no seed is set, temperatures are non-zero, and `top_p`/`top_k` are not fixed.

5. **Test D — HTTP `/v1/chat/completions` through Ollama**
   Send `mode=deterministic`, `seed=42`, identical messages twice.
   **Expected:** Output is **approximately deterministic** within a single Ollama process, but not guaranteed bit-identical across Ollama restarts or version upgrades.

---

## 4. Recommendations for Forensic-Grade Determinism

### 4.1 Immediate: add a deterministic mode to the native GGUF path

- Add `seed: int` to `InferenceRequest` (`src/egregore/interface/model_host_ports.py`).
- In `EgregoreModelHost.generate()` / `.chat()`, when a deterministic mode is requested:
  - Force `temperature=0.0`.
  - Force `top_p=1.0`, `top_k=1`.
  - Re-seed the llama.cpp RNG **before every call** (if the wrapper exposes it) or recreate the `Llama` instance with the requested seed.
- Add a `deterministic: bool` or `mode: InferenceMode` field to `InferenceRequest`.

### 4.2 Fix `ChatInferenceOrchestrator`

- Do not hard-code `temperature=0.7`.
- Accept `temperature`, `seed`, and `mode` parameters from the caller.
- Default to deterministic mode for forensic/replay use cases.

### 4.3 Fix the factory router

- Pass `seed` to `Llama(...)` in `ModelHost.get()`.
- In `_call_llm()`, always set `temperature`, `top_p=1.0`, `top_k=1` when deterministic mode is requested.
- Add an optional `deterministic: bool = True` field to `FactoryRunRequest`; default the pipeline to deterministic for code-generation.

### 4.4 Add llama.cpp determinism controls

When loading `Llama`, consider exposing:
- `use_mmap=False` / `use_mlock=True` (avoids memory-map non-determinism and swapping).
- `n_batch`, `n_ubatch` pinned to fixed values.
- `verbose=False` already set.
- For CPU-only forensic replay, consider pinning `n_threads` and setting `OMP_NUM_THREADS` / `MKL_NUM_THREADS` env vars.

### 4.5 Add property-based / regression tests

Create `tests/infrastructure/test_local_llm_adapter_determinism.py`:
```python
def test_generate_is_bit_identical_with_seed(tmp_path, gguf_path):
    adapter = LocalLlmAdapter(str(gguf_path), seed=42, n_ctx=512)
    out1 = adapter.generate("return 1+1", max_tokens=10, temperature=0.0)
    out2 = adapter.generate("return 1+1", max_tokens=10, temperature=0.0)
    assert out1["text"] == out2["text"]
    assert out1["output_hash"] == out2["output_hash"]
```

### 4.6 Document forensic limitations

- Even with `temperature=0.0` and a fixed seed, **GPU offload** (`n_gpu_layers != 0`) can introduce non-determinism in cuBLAS/hipBLAS. For forensic replay, recommend CPU-only inference or a pinned, tested GPU configuration.
- Remote backends (Anthropic, DeepSeek) cannot provide bit-identical forensic replay; treat their outputs as non-deterministic evidence.

---

## 5. Summary Table

| Path | Temperature | Seed | top_p / top_k | Deterministic? |
|------|-------------|------|---------------|----------------|
| `LocalLlmAdapter` direct, `temp=0.0` | caller (default 0.0) | load-time 42 | 1.0 / 1 | **Mostly YES** (CPU) |
| `EgregoreModelHost.generate()` | from `InferenceRequest` (default 0.7) | load-time 42 only | 1.0 / 1 | **NO** |
| `ChatInferenceOrchestrator.ask/chat()` | **0.7 hard-coded** | none | 1.0 / 1 | **NO** |
| `/v1/chat/completions` → Ollama | 0.0 in deterministic mode | request seed | 1.0 / 1 | **PARTIAL** (process-local) |
| `/v1/chat/completions` → Anthropic/DeepSeek | 0.0 in deterministic mode | not forwarded | 1.0 / 1 (Anthropic) | **NO** (remote) |
| `/api/v1/factory/...` | 0.1–0.6 from config | none | not set | **NO** |
| `LocalModelClient` (HF) | 0.0 in deterministic mode | N/A (do_sample=False) | N/A | **PARTIAL** (PyTorch ops may vary) |

---

**Final auditor opinion:** The Egregore native GGUF model host **does not currently guarantee deterministic outputs**. The deterministic intent is visible in domain models (`InferenceMode.DETERMINISTIC`, `seed=42` in adapters) but the actual orchestration layer defaults to non-deterministic sampling and lacks per-call seed control. A forensic-grade deterministic mode is feasible and should be added, tested, and documented before model outputs are treated as replayable evidence.
