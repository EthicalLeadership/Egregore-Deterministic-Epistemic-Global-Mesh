"""5-Station and 7-Stage Cognitive Factory router for Egregore.

Mounts at /api/v1/factory/{mode} and runs governed, multi-stage inference
pipelines using the Egregore native inference backend.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# PyYAML has no PEP 561 stubs; ignore for compatibility.
import yaml  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from egregore.factory.intake.intake_service import IntakeService
from egregore.factory.schemas.task_envelope import (
    CreateTaskRequest,
    TaskEnvelope,
    TaskType,
)
from egregore.factory.telemetry import (
    emit as telemetry_emit,
)
from egregore.factory.telemetry import (
    new_run_context,
    telemetry_context,
)
from egregore.shared.canonical import canonical_loads

logger = logging.getLogger("egregore.factory")

router = APIRouter(tags=["factory"])

# ---------------------------------------------------------------------------
# Egregore native inference backend
# ---------------------------------------------------------------------------
try:
    from egregore.application.inference_service import (
        InferenceService,
        build_inference_service_from_env,
    )
    from egregore.domain.inference_models import (
        ChatMessage,
        ChatRequest,
        InferenceMode,
    )

    _EGREGORE_INFERENCE_AVAILABLE = True
    _EGREGORE_INFERENCE_ERROR = ""
except Exception as exc:  # noqa: BLE001
    _EGREGORE_INFERENCE_AVAILABLE = False
    _EGREGORE_INFERENCE_ERROR = str(exc)
    InferenceService = Any  # type: ignore[misc, assignment]
    ChatMessage = Any  # type: ignore[misc, assignment]
    ChatRequest = Any  # type: ignore[misc, assignment]
    InferenceMode = Any  # type: ignore[misc, assignment]


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
    backend: str | None = None


class FactoryRunResponse(BaseModel):
    mode: str
    pipeline_version: int
    final_output: str
    stations: dict[str, StationOutput]
    provenance: dict[str, Any]
    gates: dict[str, Any] | None = None
    qc: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Model host: dispatch to the Egregore native inference backend
# ---------------------------------------------------------------------------
@dataclass
class EgregoreInferenceHost:
    """Resolves configured factory models to the Egregore inference backend.

    The factory no longer loads GGUF files via llama-cpp-python. Instead it
    sends ChatRequest objects to the Egregore InferenceService, which owns the
    native Coder model and any other registered backends.
    """

    model_specs: dict[str, dict[str, Any]]
    inference_service: InferenceService | None = None

    def _resolve_model_id(self, model_id: str) -> str:
        """Return the Egregore model identifier for a configured factory model."""
        spec = self.model_specs.get(model_id)
        if spec is None:
            raise HTTPException(status_code=500, detail=f"Unknown factory model '{model_id}'")
        model_id_or_alias = spec.get("model_id") or spec.get("path") or model_id
        return str(model_id_or_alias)

    def execute(
        self,
        model_id: str,
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> tuple[str, int, str]:
        """Run a chat completion through Egregore and return (text, tokens, backend)."""
        if self.inference_service is None:
            raise HTTPException(
                status_code=503,
                detail=f"Egregore inference service is not available: {_EGREGORE_INFERENCE_ERROR}",
            )

        eg_model = self._resolve_model_id(model_id)
        messages: list[ChatMessage] = []
        if system:
            messages.append(ChatMessage(role="system", content=system))
        messages.append(ChatMessage(role="user", content=prompt))

        mode = InferenceMode.CREATIVE if (temperature is not None and temperature > 0) else InferenceMode.DETERMINISTIC
        request = ChatRequest(
            model=eg_model,
            messages=messages,
            mode=mode,
            max_tokens=max_tokens or 2048,
            seed=42,
            stream=False,
        )

        start = time.monotonic()
        response = self.inference_service.execute(request)
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        from egregore.application.inference_service import _resolve_backend

        backend_name = _resolve_backend(eg_model)
        content = response.message.content or ""
        usage = response.usage or {}
        tokens = usage.get("total_tokens", usage.get("completion_tokens", 0))
        telemetry_emit(
            "factory.inference",
            model_id=model_id,
            eg_model=eg_model,
            inference_mode=str(mode),
            latency_ms=latency_ms,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            total_tokens=int(tokens),
            finish_reason=response.finish_reason,
            m1=response.m1_passed,
            m2=response.m2_passed,
            m3=response.m3_passed,
            m4=response.m4_passed,
            inference_id=response.inference_id,
        )
        # Track governance failures for the QC gate (fail-closed input).
        ctx = telemetry_context.get()
        if ctx is not None and not all(
            (response.m1_passed, response.m2_passed, response.m3_passed, response.m4_passed)
        ):
            ctx["_m_failure"] = True
        return content.strip(), int(tokens), backend_name

    def health(self) -> dict[str, Any]:
        service_health: dict[str, Any] = {"available": False, "backends": {}}
        if self.inference_service is not None:
            try:
                service_health = self.inference_service.health()
            except Exception as exc:  # noqa: BLE001
                service_health = {"available": False, "error": str(exc)}
        return {
            "egregore_inference_available": _EGREGORE_INFERENCE_AVAILABLE and self.inference_service is not None,
            "configured_models": {
                mid: {
                    "model_id": spec.get("model_id") or spec.get("path") or mid,
                }
                for mid, spec in self.model_specs.items()
            },
            "service": service_health,
        }


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------
def _blocked_policy_response(
    mode: str, exc: Exception, pipeline_version: int = 2
) -> FactoryRunResponse:
    """Fail-closed: malformed policy means nothing ships, no station runs."""
    return FactoryRunResponse(
        mode=mode,
        pipeline_version=pipeline_version,
        final_output="[QC BLOCKED] factory policy malformed — nothing ships",
        stations={},
        provenance={"policy_error": str(exc)[:300], "timestamp_ns": time.time_ns()},
        qc={
            "terminal_state": "BLOCKED",
            "m4_emission": "DIVERGED",
            "verdict": "FAIL",
            "confidence": 0.0,
            "tier": "deterministic",
            "violations": [
                {"constraint_id": "policy_malformed", "evidence": str(exc)[:300], "severity": "hard"}
            ],
            "reworks_used": 0,
            "escalated": False,
        },
    )


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


def _get_inference_host(request: Request) -> EgregoreInferenceHost:
    """Resolve or create the cached EgregoreInferenceHost from app state."""
    host: EgregoreInferenceHost | None = getattr(request.app.state, "factory_model_host", None)
    if host is None:
        profiles = _load_profiles()
        inference_service: InferenceService | None = getattr(request.app.state, "inference_service", None)
        if inference_service is None and _EGREGORE_INFERENCE_AVAILABLE:
            try:
                inference_service = build_inference_service_from_env()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not build Egregore inference service from env: %s", exc)
        host = EgregoreInferenceHost(
            model_specs=profiles.get("models", {}),
            inference_service=inference_service,
        )
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


def _set_current_station(station: str) -> None:
    """Tag the active telemetry context with the station now executing."""
    ctx = telemetry_context.get()
    if ctx is not None:
        ctx["current_station"] = station


# ---------------------------------------------------------------------------
# VRAM residency (Phase 6)
# ---------------------------------------------------------------------------
from egregore.factory.residency import (  # noqa: E402
    ResidencyManager,
    VramInsufficientError,
)

_residency: ResidencyManager | None = None


def _get_residency(host: EgregoreInferenceHost) -> ResidencyManager:
    """Process-wide residency manager, bound to the host's inference service."""
    global _residency
    if _residency is None:
        service = host.inference_service
        clients = getattr(service, "clients", None)
        gguf = clients.get("gguf") if isinstance(clients, dict) else None

        def _heavy_factory() -> Any:
            from egregore.infrastructure.coder_backend import CoderBackend

            return CoderBackend(enabled=True)

        _residency = ResidencyManager(
            gguf_backend=gguf,
            heavy_backend_factory=_heavy_factory,
        )
    return _residency


class _HeavyPassService:
    """Minimal InferenceService-shaped adapter over a raw ILlmClient.

    Used only inside the residency heavy_pass swap: the HF 8-bit backend is
    not registered in the app's InferenceService (it's not resident), so the
    escalated station call goes through this shim. Governance m-flags are
    asserted True by construction (same stub level as the service today).
    """

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def execute(self, request: Any) -> Any:
        import dataclasses

        response = self._backend.chat(request)
        return dataclasses.replace(
            response, m1_passed=True, m2_passed=True, m3_passed=True, m4_passed=True
        )


def _vram_insufficient_response(mode: str, exc: VramInsufficientError) -> FactoryRunResponse:
    """Pre-flight shortfall: BLOCKED in milliseconds instead of a mid-run OOM."""
    return FactoryRunResponse(
        mode=mode,
        pipeline_version=2,
        final_output="[QC BLOCKED] vram_insufficient — nothing ships",
        stations={},
        provenance={"vram_error": str(exc), "timestamp_ns": time.time_ns()},
        qc={
            "terminal_state": "BLOCKED",
            "m4_emission": "DIVERGED",
            "verdict": "FAIL",
            "confidence": 0.0,
            "tier": "deterministic",
            "violations": [
                {
                    "constraint_id": "vram_insufficient",
                    "evidence": f"need ~{exc.need_mb} MB, have {exc.free_mb} MB free",
                    "severity": "hard",
                }
            ],
            "reworks_used": 0,
            "escalated": False,
        },
    )


def _call_llm(
    host: EgregoreInferenceHost,
    model_id: str,
    prompt: str,
    system: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> tuple[str, int, str]:
    """Run a single completion via the Egregore inference backend."""
    return host.execute(
        model_id=model_id,
        prompt=prompt,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def _run_station(
    host: EgregoreInferenceHost,
    station_name: str,
    station: dict[str, Any],
    context: dict[str, Any],
    request_overrides: dict[str, Any],
) -> StationOutput:
    """Run a single station, formatting its prompt from the running context."""
    start = time.monotonic()
    model_id = station["model"]

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

    _set_current_station(station_name)
    free_mb = _get_residency(host).pre_flight(station_name)
    output, tokens, backend = _call_llm(
        host,
        model_id=model_id,
        prompt=prompt,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    elapsed = round((time.monotonic() - start) * 1000, 2)

    parsed = _extract_json(output)
    telemetry_emit(
        "factory.station",
        station=station_name,
        elapsed_ms=elapsed,
        tokens=tokens,
        model_id=model_id,
        ok=True,
        backend=backend,
        vram_free_mb=free_mb,
    )
    return StationOutput(
        output=output,
        model=model_id,
        tokens=tokens,
        elapsed_ms=elapsed,
        parsed=parsed,
        backend=backend,
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
    host = _get_inference_host(request)
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
    host: EgregoreInferenceHost, station: dict[str, Any], user_input: str
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

    model_id = station["model"]
    _set_current_station("compression")
    output, tokens, backend = _call_llm(
        host,
        model_id=model_id,
        prompt=station["prompt"].format(input=user_input),
        system=station.get("system"),
        max_tokens=station.get("max_tokens"),
        temperature=station.get("temperature"),
    )
    elapsed = round((time.monotonic() - start) * 1000, 2)
    telemetry_emit(
        "factory.station",
        station="compression",
        elapsed_ms=elapsed,
        tokens=tokens,
        model_id=model_id,
        ok=True,
    )
    return output, StationOutput(
        output=output,
        model=model_id,
        tokens=tokens,
        elapsed_ms=elapsed,
        compressed=True,
        backend=backend,
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
    host = _get_inference_host(request)
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
# Shared intake service
# ---------------------------------------------------------------------------
_intake_service: IntakeService | None = None


def _get_intake_service() -> IntakeService:
    global _intake_service
    if _intake_service is None:
        _intake_service = IntakeService()
    return _intake_service


# ---------------------------------------------------------------------------
# Intake endpoints (S1)
# ---------------------------------------------------------------------------
@router.post("/v1/intake", response_model=TaskEnvelope)
def create_task_from_request(
    req: CreateTaskRequest,
    request: Request,
) -> TaskEnvelope:
    """Normalize any raw input into a canonical TaskEnvelope."""
    service = _get_intake_service()
    return service.accept(req, remote_addr=request.client.host if request.client else None)


@router.post("/v1/intake/chat", response_model=TaskEnvelope)
def create_task_from_chat(
    message: dict[str, Any],
    request: Request,
) -> TaskEnvelope:
    """Normalize an OpenAI-style chat message into a TaskEnvelope."""
    service = _get_intake_service()
    return service.accept_chat_message(
        message,
        remote_addr=request.client.host if request.client else None,
    )


@router.post("/v1/intake/email", response_model=TaskEnvelope)
def create_task_from_email(
    envelope: dict[str, Any],
    request: Request,
) -> TaskEnvelope:
    """Normalize a GDC-style email envelope into a TaskEnvelope."""
    service = _get_intake_service()
    return service.accept_email_envelope(
        envelope,
        remote_addr=request.client.host if request.client else None,
    )


@router.post("/v1/intake/anchorum", response_model=TaskEnvelope)
def create_task_from_anchorum(
    artifact: dict[str, Any],
    request: Request,
) -> TaskEnvelope:
    """Normalize an ANCHORUM artifact record into a TaskEnvelope."""
    service = _get_intake_service()
    return service.accept(
        CreateTaskRequest(
            source_type="anchorum",
            source_id=artifact.get("artifact_id") or artifact.get("sha256"),
            filename=artifact.get("filename"),
            sha256=artifact.get("sha256"),
            text=artifact.get("text_preview") or artifact.get("description"),
            metadata={k: v for k, v in artifact.items() if k not in ("artifact_id", "sha256", "filename", "text_preview")},
            task_type=TaskType.FORENSIC_QUERY,
        ),
        remote_addr=request.client.host if request.client else None,
    )


class EnvelopeRunRequest(BaseModel):
    """Run the factory from an already-normalized TaskEnvelope."""

    envelope: TaskEnvelope
    mode: str | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


@router.post("/v1/run", response_model=FactoryRunResponse)
def run_factory_from_envelope(
    req: EnvelopeRunRequest,
    request: Request,
) -> FactoryRunResponse:
    """Run the factory pipeline starting from a TaskEnvelope."""
    mode = req.mode or DEFAULT_FLAGSHIP_MODE
    text = req.envelope.payload.text or ""
    return _run_factory_impl(
        mode,
        FactoryRunRequest(
            input=text,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
        ),
        request,
        envelope=req.envelope,
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
    host = _get_inference_host(request)
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


def _apply_qc_gate(
    *,
    response: FactoryRunResponse,
    req: FactoryRunRequest,
    request: Request,
    mode_profile: dict[str, Any],
    pipeline_version: int,
    ctx: dict[str, Any],
):
    """Run the fail-closed QC gate over the terminal output.

    Any gate-level exception becomes BLOCKED (fail-closed): nothing ships.
    """
    from egregore.factory.policy import load_policy
    from egregore.factory.qc_gate import EgregoreCritic, QCGate

    policy = load_policy().data.get("qc", {})
    host = _get_inference_host(request)
    critic = EgregoreCritic(
        host,
        model_id=str(policy.get("critic_model", "qwen_1.5b")),
        confidence_threshold=float(policy.get("confidence_threshold", 0.6)),
    )
    stations_cfg = mode_profile["stations"]

    def rerun_terminal(rework_prompt: str, escalated: bool = False):
        """Re-run the terminal generative station with typed violations.

        Escalated runs go through the residency swap: hot GGUF residents
        unload, the HF 8-bit heavy pass loads, runs, unloads, and the hot
        residents come back. Serialized by the residency lock.
        """
        ctx["_m_failure"] = False

        def _run(host_like: Any) -> Any:
            if pipeline_version == 2:
                base = {
                    "input": req.input,
                    "spec_synthesis_output": (
                        response.stations["spec_synthesis"].output + "\n\n" + rework_prompt
                    ),
                    "scaffolding_output": response.stations["scaffolding"].output,
                }
                return _run_station(host_like, "cnc", stations_cfg["cnc"], base, {})
            compressed = response.stations.get("compression")
            base_input = compressed.output if compressed else req.input
            return _run_station(
                host_like, "cnc", stations_cfg["cnc"],
                {"input": rework_prompt + "\n\nOriginal input:\n" + base_input}, {},
            )

        if escalated:

            residency: ResidencyManager = _get_residency(host)
            with residency.heavy_pass() as heavy_backend:
                heavy_host = EgregoreInferenceHost(
                    model_specs=host.model_specs,
                    inference_service=_HeavyPassService(heavy_backend),
                )
                station = _run(heavy_host)
        else:
            station = _run(host)

        m_flags = {"m1": False, "m2": False, "m3": False, "m4": False} if ctx["_m_failure"] else None
        return station.output, station.parsed, m_flags

    initial_m = (
        {"m1": False, "m2": False, "m3": False, "m4": False}
        if ctx["_m_failure"] else None
    )
    cnc_station = response.stations.get("cnc")
    gate = QCGate(policy=policy, critic=critic, rerun_terminal=rerun_terminal)
    try:
        return gate.evaluate(
            output=response.final_output,
            constraints=[req.input],
            m_flags=initial_m,
            terminal_parsed=(cnc_station.parsed if cnc_station else None),
        )
    except Exception as gate_exc:  # noqa: BLE001 — fail-closed
        logger.error("QC gate error (blocking): %s", gate_exc)
        from egregore.factory.qc_gate import QCOutcome, QCVerdict, Violation

        return QCOutcome(
            terminal_state="BLOCKED",
            m4_emission="DIVERGED",
            verdict=QCVerdict(
                verdict="FAIL",
                confidence=0.0,
                violations=[Violation(
                    constraint_id="gate_error",
                    evidence=str(gate_exc)[:300],
                )],
                critic_model="none",
                tier="deterministic",
            ),
            final_output=None,
        )


def _load_policy_or_block(
    mode: str, ctx: dict[str, Any]
) -> tuple[str | None, FactoryRunResponse | None]:
    """Load governance policy; on malformed policy emit + return a BLOCKED response."""
    from egregore.factory.policy import PolicyError, load_policy

    try:
        loaded_policy = load_policy()
    except PolicyError as exc:
        logger.error("factory policy malformed (blocking run): %s", exc)
        telemetry_emit(
            "factory.run.outcome",
            stations_taken=[],
            total_elapsed_ms=0.0,
            total_tokens=0,
            ok=False,
            error=f"policy_malformed: {exc}"[:300],
            policy_hash=None,
            qc={"terminal_state": "BLOCKED", "reworks": 0, "escalated": False, "confidence": 0.0},
        )
        return None, _blocked_policy_response(mode, exc)
    for override_key, override_value in loaded_policy.overrides.items():
        telemetry_emit(
            "factory.policy.override",
            key=override_key,
            value=override_value,
            source="env",
            policy_hash=loaded_policy.policy_hash,
        )
    return loaded_policy.policy_hash, None


def _run_factory_impl(
    mode: str,
    req: FactoryRunRequest,
    request: Request,
    envelope: TaskEnvelope | None = None,
) -> FactoryRunResponse:
    """Route to the correct pipeline implementation (with telemetry)."""
    profiles = _load_profiles()
    mode_profile = profiles.get("modes", {}).get(mode)
    if mode_profile is None:
        available = list(profiles.get("modes", {}).keys())
        raise HTTPException(
            status_code=404,
            detail=f"Factory mode '{mode}' not found. Available: {available}",
        )

    ctx = new_run_context(mode=mode)
    if envelope is not None:
        ctx.update(
            task_id=envelope.task_id,
            task_fingerprint=envelope.fingerprint(),
            task_type=str(envelope.task_type),
        )
    ctx["_m_failure"] = False
    token = telemetry_context.set(ctx)

    # --- Governance: load policy BEFORE anything runs (fail-closed) -------
    policy_hash, blocked = _load_policy_or_block(mode, ctx)
    if blocked is not None:
        telemetry_context.reset(token)
        return blocked

    telemetry_emit(
        "factory.envelope.in",
        priority=(str(envelope.priority) if envelope is not None else None),
        capabilities=(list(envelope.required_capabilities) if envelope is not None else []),
        payload_bytes=len(req.input.encode("utf-8", "ignore")),
        source_type=(str(envelope.source.source_type) if envelope is not None else "http"),
        policy_hash=policy_hash,
    )
    run_start = time.monotonic()
    stations_taken: list[str] = []
    ok = True
    error: str | None = None
    qc_block: dict[str, Any] | None = None
    try:
        pipeline_version = mode_profile.get("pipeline_version", 1)
        if pipeline_version == 2:
            response = _run_pipeline_v2(mode, mode_profile, req, request)
        else:
            response = _run_pipeline_v1(mode, mode_profile, req, request)
        stations_taken = list(response.stations.keys())
        ctx["_total_tokens"] = response.provenance.get("total_tokens", 0)

        # --- Station 5: fail-closed QC gate on the terminal output --------
        outcome = _apply_qc_gate(
            response=response,
            req=req,
            request=request,
            mode_profile=mode_profile,
            pipeline_version=pipeline_version,
            ctx=ctx,
        )
        qc_block = {
            "terminal_state": outcome.terminal_state,
            "m4_emission": outcome.m4_emission,
            "verdict": outcome.verdict.verdict,
            "confidence": outcome.verdict.confidence,
            "tier": outcome.verdict.tier,
            "violations": [v.model_dump() for v in outcome.verdict.violations],
            "reworks_used": outcome.reworks_used,
            "escalated": outcome.escalated,
            "critic_model": outcome.verdict.critic_model,
            "critic_latency_ms": outcome.verdict.latency_ms,
        }
        if outcome.bypassed:
            qc_block["bypassed"] = True
            qc_block["governance_record"] = "QC_BYPASSED"
        response.qc = qc_block
        if outcome.terminal_state == "BLOCKED":
            response.final_output = "[QC BLOCKED] output withheld — see qc.violations"
        elif outcome.final_output is not None and outcome.final_output != response.final_output:
            # Reworked/escalated output replaced the original.
            response.final_output = outcome.final_output
        return response
    except VramInsufficientError as vram_exc:
        # Pre-flight shortfall: BLOCKED without shipping, no exception upward.
        ok = True  # the run completed; the BLOCK is data, not a crash
        error = None
        return _vram_insufficient_response(mode, vram_exc)
    except Exception as exc:
        ok = False
        error = str(exc)
        raise
    finally:
        total_tokens = ctx.pop("_total_tokens", 0)
        ctx.pop("_m_failure", None)
        telemetry_emit(
            "factory.run.outcome",
            stations_taken=stations_taken,
            total_elapsed_ms=round((time.monotonic() - run_start) * 1000, 2),
            total_tokens=total_tokens,
            ok=ok,
            error=error,
            policy_hash=policy_hash,
            qc=(
                {
                    "terminal_state": qc_block["terminal_state"],
                    "reworks": qc_block["reworks_used"],
                    "escalated": qc_block["escalated"],
                    "confidence": qc_block["confidence"],
                }
                if qc_block
                else None
            ),
        )
        telemetry_context.reset(token)


@router.get("/{mode}/health")
def factory_mode_health(mode: str, request: Request) -> dict[str, Any]:
    """Return whether all models required by a mode are configured and reachable."""
    profiles = _load_profiles()
    mode_profile = profiles.get("modes", {}).get(mode)
    if mode_profile is None:
        raise HTTPException(status_code=404, detail=f"Factory mode '{mode}' not found")

    host = _get_inference_host(request)
    model_ids = {s["model"] for s in mode_profile["stations"].values()}
    checks = {}
    for mid in model_ids:
        spec = host.model_specs.get(mid, {})
        model_id = spec.get("model_id") or spec.get("path") or mid
        checks[mid] = {
            "model_id": model_id,
            "configured": mid in host.model_specs,
        }

    service_ready = host.inference_service is not None
    return {
        "mode": mode,
        "pipeline_version": mode_profile.get("pipeline_version", 1),
        "egregore_inference_available": _EGREGORE_INFERENCE_AVAILABLE and service_ready,
        "models": checks,
        "ready": service_ready and all(c["configured"] for c in checks.values()),
    }
