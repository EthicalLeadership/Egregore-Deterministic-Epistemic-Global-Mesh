"""5-Station and 7-Stage Cognitive Factory router for Egregore.

Mounts at /api/v1/factory/{mode} and runs governed, multi-stage inference
pipelines using local GGUF models via llama-cpp-python.

Pipeline versions:
  v1 (pipeline_version: 1) — classic 5-station wheel:
    intake → parts_mfg → compression → cnc → qc

  v2 (pipeline_version: 2) — hardened 7-stage assembly line:
    spec_synthesis → scaffolding → cnc → static_analysis → dynamic_test →
    moral_compliance → final_qc
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# PyYAML has no PEP 561 stubs; ignore for compatibility.
import yaml  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from egregore.shared.canonical import canonical_loads

logger = logging.getLogger("egregore.factory")

router = APIRouter(tags=["factory"])

# ---------------------------------------------------------------------------
# Optional llama-cpp-python import with graceful degradation
# ---------------------------------------------------------------------------
try:
    from llama_cpp import Llama

    _LLAMA_AVAILABLE = True
except Exception as exc:  # noqa: BLE001
    _LLAMA_AVAILABLE = False
    _LLAMA_IMPORT_ERROR = str(exc)


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------
class FactoryRunRequest(BaseModel):
    input: str = Field(min_length=1, max_length=50000)
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    stream: bool = Field(default=False)


class StationOutput(BaseModel):
    output: str
    model: str
    tokens: int = 0
    elapsed_ms: float = 0.0
    compressed: bool | None = None
    verdict: str | None = None
    parsed: dict[str, Any] | None = None


class FactoryRunResponse(BaseModel):
    mode: str
    pipeline_version: int
    final_output: str
    stations: dict[str, StationOutput]
    provenance: dict[str, Any]
    gates: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Model host: lazy-load and cache GGUF models
# ---------------------------------------------------------------------------
@dataclass
class ModelHost:
    """Loads Llama models on first use and caches them for the process lifetime."""

    model_specs: dict[str, dict[str, Any]]
    _cache: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def get(self, model_id: str) -> Any:
        if model_id in self._cache:
            return self._cache[model_id]

        spec = self.model_specs.get(model_id)
        if spec is None:
            raise HTTPException(status_code=500, detail=f"Unknown model '{model_id}'")

        path = Path(spec["path"])
        if not path.exists():
            raise HTTPException(
                status_code=503,
                detail=f"Model '{model_id}' not found at {path}. Run scripts/download_factory_models.sh",
            )

        if not _LLAMA_AVAILABLE:
            raise HTTPException(
                status_code=503,
                detail=f"llama-cpp-python is not available: {_LLAMA_IMPORT_ERROR}",
            )

        logger.info("Loading model %s from %s", model_id, path)
        start = time.monotonic()
        llm = Llama(
            model_path=str(path),
            n_ctx=spec.get("n_ctx", 8192),
            n_gpu_layers=spec.get("n_gpu_layers", -1),
            chat_format=spec.get("chat_format", None),
            verbose=False,
        )
        elapsed = (time.monotonic() - start) * 1000
        logger.info("Model %s loaded in %.1f ms", model_id, elapsed)
        self._cache[model_id] = llm
        return llm

    def health(self) -> dict[str, Any]:
        return {
            "llama_cpp_available": _LLAMA_AVAILABLE,
            "cached_models": list(self._cache.keys()),
            "configured_models": {
                mid: {
                    "path": spec.get("path"),
                    "exists": Path(spec.get("path", "")).exists(),
                }
                for mid, spec in self.model_specs.items()
            },
        }


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------
def _load_profiles() -> dict[str, Any]:
    """Load factory profiles from config/factory_profiles_v2.yaml."""
    candidate_paths = [
        Path(__file__).resolve().parents[3] / "config" / "factory_profiles_v2.yaml",
        Path("/opt/egregore/config/factory_profiles_v2.yaml"),
        Path("config/factory_profiles_v2.yaml"),
    ]
    for path in candidate_paths:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                # justification: yaml.safe_load is untyped; runtime contract is dict.
                return yaml.safe_load(f)  # type: ignore[no-any-return]
    raise HTTPException(status_code=500, detail="factory_profiles_v2.yaml not found")


def _get_model_host(request: Request) -> ModelHost:
    """Resolve or create the cached ModelHost from app state."""
    host: ModelHost | None = getattr(request.app.state, "factory_model_host", None)
    if host is None:
        profiles = _load_profiles()
        host = ModelHost(model_specs=profiles.get("models", {}))
        request.app.state.factory_model_host = host
    return host


# ---------------------------------------------------------------------------
# Inference helpers
# ---------------------------------------------------------------------------
def _extract_json(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction from model output (fenced or raw)."""
    text = text.strip()
    parsed: dict[str, Any] | None = None

    # Try fenced JSON code block
    fence_match = re.search(
        r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE
    )
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            parsed = canonical_loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    # Try raw JSON object / array
    raw_match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if raw_match:
        try:
            parsed = canonical_loads(raw_match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    # Try the entire text as JSON
    try:
        parsed = canonical_loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    return None


def _call_llm(
    llm: Any,
    prompt: str,
    system: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> tuple[str, int]:
    """Run a single completion via create_chat_completion and return text + tokens."""
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs: dict[str, Any] = {}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if temperature is not None:
        kwargs["temperature"] = temperature

    response = llm.create_chat_completion(messages=messages, **kwargs)
    choice = response["choices"][0]
    content = choice["message"].get("content", "")
    usage = response.get("usage", {})
    tokens = usage.get("total_tokens", usage.get("completion_tokens", 0))
    return content.strip(), int(tokens)


def _run_station(
    host: ModelHost,
    station_name: str,
    station: dict[str, Any],
    context: dict[str, Any],
    request_overrides: dict[str, Any],
) -> StationOutput:
    """Run a single station, formatting its prompt from the running context."""
    start = time.monotonic()
    llm = host.get(station["model"])

    prompt_template = station.get("prompt", "")
    try:
        prompt = prompt_template.format(**context)
    except KeyError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Station '{station_name}' prompt missing placeholder: {exc}",
        ) from exc

    system = station.get("system")
    max_tokens = request_overrides.get("max_tokens") or station.get("max_tokens")
    temperature = request_overrides.get("temperature")
    if temperature is None:
        temperature = station.get("temperature")

    output, tokens = _call_llm(
        llm,
        prompt=prompt,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    elapsed = round((time.monotonic() - start) * 1000, 2)

    parsed = _extract_json(output)
    return StationOutput(
        output=output,
        model=station["model"],
        tokens=tokens,
        elapsed_ms=elapsed,
        parsed=parsed,
    )


# ---------------------------------------------------------------------------
# v1 pipeline (5-station wheel)
# ---------------------------------------------------------------------------
def _run_pipeline_v1(
    mode: str,
    mode_profile: dict[str, Any],
    req: FactoryRunRequest,
    request: Request,
) -> FactoryRunResponse:
    """Run the classic 5-station factory pipeline."""
    host = _get_model_host(request)
    stations_cfg = mode_profile["stations"]
    station_results: dict[str, StationOutput] = {}
    pipeline_start = time.monotonic()

    context: dict[str, Any] = {"input": req.input}

    intake = _run_station(host, "intake", stations_cfg["intake"], context, {})
    station_results["intake"] = intake

    parts = _run_station(host, "parts_mfg", stations_cfg["parts_mfg"], context, {})
    station_results["parts_mfg"] = parts

    compressed_raw, compression = _station_compression(
        host, stations_cfg["compression"], req.input
    )
    station_results["compression"] = compression

    cnc_context = {**context, "input": compressed_raw}
    cnc = _run_station(host, "cnc", stations_cfg["cnc"], cnc_context, {})
    station_results["cnc"] = cnc

    qc_context = {**context, "input": req.input, "cnc_output": cnc.output}
    qc = _run_station(host, "qc", stations_cfg["qc"], qc_context, {})
    qc.verdict = "PASS" if "PASS" in qc.output.upper() else "FAIL"
    station_results["qc"] = qc

    final_output = cnc.output
    if qc.verdict == "FAIL":
        final_output = "[QC FLAGGED] " + final_output

    total_ms = round((time.monotonic() - pipeline_start) * 1000, 2)

    return FactoryRunResponse(
        mode=mode,
        pipeline_version=1,
        final_output=final_output,
        stations=station_results,
        provenance={
            "mode": mode,
            "mode_name": mode_profile.get("name"),
            "total_elapsed_ms": total_ms,
            "total_tokens": sum(s.tokens for s in station_results.values()),
            "models_used": list(
                {s.model for s in station_results.values() if s.tokens > 0}
            ),
            "timestamp_ns": time.time_ns(),
            "intake_label": intake.output,
            "qc_verdict": qc.verdict,
        },
    )


def _station_compression(
    host: ModelHost, station: dict[str, Any], user_input: str
) -> tuple[str, StationOutput]:
    start = time.monotonic()
    threshold = station.get("threshold_chars", 500)
    if len(user_input) <= threshold:
        return user_input, StationOutput(
            output=user_input,
            model=station["model"],
            tokens=0,
            elapsed_ms=round((time.monotonic() - start) * 1000, 2),
            compressed=False,
        )

    llm = host.get(station["model"])
    output, tokens = _call_llm(
        llm,
        prompt=station["prompt"].format(input=user_input),
        system=station.get("system"),
        max_tokens=station.get("max_tokens"),
        temperature=station.get("temperature"),
    )
    return output, StationOutput(
        output=output,
        model=station["model"],
        tokens=tokens,
        elapsed_ms=round((time.monotonic() - start) * 1000, 2),
        compressed=True,
    )


# ---------------------------------------------------------------------------
# v2 pipeline (7-stage hardened assembly line)
# ---------------------------------------------------------------------------
def _run_pipeline_v2(
    mode: str,
    mode_profile: dict[str, Any],
    req: FactoryRunRequest,
    request: Request,
) -> FactoryRunResponse:
    """Run the hardened 7-stage assembly line."""
    host = _get_model_host(request)
    stations_cfg = mode_profile["stations"]
    station_results: dict[str, StationOutput] = {}
    pipeline_start = time.monotonic()

    context: dict[str, Any] = {"input": req.input}
    overrides = {"max_tokens": req.max_tokens, "temperature": req.temperature}

    # Stage 1: Specification synthesis
    spec = _run_station(
        host, "spec_synthesis", stations_cfg["spec_synthesis"], context, overrides
    )
    station_results["spec_synthesis"] = spec
    context["spec_synthesis_output"] = spec.output
    spec_data = spec.parsed or {}

    # Stage 2: Scaffolding
    scaffold = _run_station(
        host, "scaffolding", stations_cfg["scaffolding"], context, overrides
    )
    station_results["scaffolding"] = scaffold
    context["scaffolding_output"] = scaffold.output

    # Stage 3: CNC core generation (JSON with module/test/readme)
    cnc = _run_station(host, "cnc", stations_cfg["cnc"], context, overrides)
    station_results["cnc"] = cnc

    cnc_data = cnc.parsed or {}
    module_code = cnc_data.get("module", "")
    test_code = cnc_data.get("test", "")
    readme_code = cnc_data.get("readme", "")

    context["module_code"] = module_code
    context["test_code"] = test_code
    context["readme_code"] = readme_code
    context["cnc_output"] = cnc.output

    # Derive edge cases text for dynamic test stage
    edge_cases = spec_data.get("edge_cases", [])
    if isinstance(edge_cases, list):
        context["edge_cases"] = "\n".join(f"- {e}" for e in edge_cases)
    else:
        context["edge_cases"] = str(edge_cases)

    # Stage 4: Static analysis
    static_analysis = _run_station(
        host, "static_analysis", stations_cfg["static_analysis"], context, overrides
    )
    station_results["static_analysis"] = static_analysis
    context["static_analysis_output"] = static_analysis.output

    # Stage 5: Dynamic test audit
    dynamic_test = _run_station(
        host, "dynamic_test", stations_cfg["dynamic_test"], context, overrides
    )
    station_results["dynamic_test"] = dynamic_test
    context["dynamic_test_output"] = dynamic_test.output

    # Stage 6: Moral / legal compliance
    moral_compliance = _run_station(
        host, "moral_compliance", stations_cfg["moral_compliance"], context, overrides
    )
    station_results["moral_compliance"] = moral_compliance
    context["moral_compliance_output"] = moral_compliance.output

    # Stage 7: Final QC aggregation
    final_qc = _run_station(
        host, "final_qc", stations_cfg["final_qc"], context, overrides
    )
    station_results["final_qc"] = final_qc

    # Determine final verdict from final_qc JSON if available
    final_qc_data = final_qc.parsed or {}
    verdict = str(final_qc_data.get("verdict", "FAIL")).upper()
    if verdict not in {"PASS", "FAIL"}:
        verdict = "FAIL"

    final_output = module_code
    if verdict == "FAIL":
        final_output = "[QC FLAGGED] " + final_output

    total_ms = round((time.monotonic() - pipeline_start) * 1000, 2)

    return FactoryRunResponse(
        mode=mode,
        pipeline_version=2,
        final_output=final_output,
        stations=station_results,
        gates={
            "static_analysis": static_analysis.parsed,
            "dynamic_test": dynamic_test.parsed,
            "moral_compliance": moral_compliance.parsed,
            "final_qc": final_qc_data,
        },
        provenance={
            "mode": mode,
            "mode_name": mode_profile.get("name"),
            "total_elapsed_ms": total_ms,
            "total_tokens": sum(s.tokens for s in station_results.values()),
            "models_used": list(
                {s.model for s in station_results.values() if s.tokens > 0}
            ),
            "timestamp_ns": time.time_ns(),
            "qc_verdict": verdict,
        },
    )


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------
DEFAULT_FLAGSHIP_MODE = "coding_factory"


@router.get("/modes")
def list_factory_modes() -> dict[str, Any]:
    """List all available factory wheel positions."""
    profiles = _load_profiles()
    modes = {}
    default_mode = None
    for name, profile in profiles.get("modes", {}).items():
        modes[name] = {
            "name": profile.get("name"),
            "description": profile.get("description"),
            "stations": list(profile.get("stations", {}).keys()),
            "default": bool(profile.get("default", False)),
            "pipeline_version": profile.get("pipeline_version", 1),
        }
        if profile.get("default"):
            default_mode = name
    return {"modes": modes, "default_mode": default_mode or DEFAULT_FLAGSHIP_MODE}


@router.get("/health")
def factory_health(request: Request) -> dict[str, Any]:
    """Overall factory health and configured models."""
    host = _get_model_host(request)
    return host.health()


@router.post("", response_model=FactoryRunResponse)
def run_default_factory(
    req: FactoryRunRequest,
    request: Request,
) -> FactoryRunResponse:
    """Run the most sophisticated factory mode (coding_factory) by default."""
    return _run_factory_impl(DEFAULT_FLAGSHIP_MODE, req, request)


@router.post("/{mode}", response_model=FactoryRunResponse)
def run_factory(
    mode: str,
    req: FactoryRunRequest,
    request: Request,
) -> FactoryRunResponse:
    """Run the factory pipeline for the selected mode (wheel position)."""
    return _run_factory_impl(mode, req, request)


def _run_factory_impl(
    mode: str,
    req: FactoryRunRequest,
    request: Request,
) -> FactoryRunResponse:
    """Route to the correct pipeline implementation."""
    profiles = _load_profiles()
    mode_profile = profiles.get("modes", {}).get(mode)
    if mode_profile is None:
        available = list(profiles.get("modes", {}).keys())
        raise HTTPException(
            status_code=404,
            detail=f"Factory mode '{mode}' not found. Available: {available}",
        )

    pipeline_version = mode_profile.get("pipeline_version", 1)
    if pipeline_version == 2:
        return _run_pipeline_v2(mode, mode_profile, req, request)
    return _run_pipeline_v1(mode, mode_profile, req, request)


@router.get("/{mode}/health")
def factory_mode_health(mode: str, request: Request) -> dict[str, Any]:
    """Return whether all models required by a mode are present and loadable."""
    profiles = _load_profiles()
    mode_profile = profiles.get("modes", {}).get(mode)
    if mode_profile is None:
        raise HTTPException(status_code=404, detail=f"Factory mode '{mode}' not found")

    host = _get_model_host(request)
    model_ids = {s["model"] for s in mode_profile["stations"].values()}
    checks = {}
    for mid in model_ids:
        spec = host.model_specs.get(mid, {})
        path = Path(spec.get("path", ""))
        checks[mid] = {
            "path": str(path),
            "exists": path.exists(),
        }

    return {
        "mode": mode,
        "pipeline_version": mode_profile.get("pipeline_version", 1),
        "llama_cpp_available": _LLAMA_AVAILABLE,
        "models": checks,
        "ready": all(c["exists"] for c in checks.values()),
    }
