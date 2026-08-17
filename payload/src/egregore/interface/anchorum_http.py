"""Lightweight plain-HTTP app for the ANCHORUM customer-facing site.

Unlike the full bootstrap, this app does NOT load the Egregore native Coder
model. It only serves the dashboard, static assets, and ANCHORUM page. API
calls that need inference should go to the Core API on port 8002 or the HTTPS
bootstrap on port 8443.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from egregore.http_api.http.middleware.api_key_middleware import APIKeyMiddleware
from egregore.interface.anchorum_router import router as anchorum_router
from egregore.interface.dashboard import DashboardService, DashboardServiceProvider
from egregore.interface.dashboard import router as dashboard_router
from egregore.interface.dashboard.freeze_middleware import FreezeGateMiddleware
from egregore.shared.paths import repo_root

logger = logging.getLogger("egregore.anchorum_http")

CORE_API_URL = os.environ.get("EGREGORE_CORE_API_URL", "http://127.0.0.1:8002")
EMS_URL = os.environ.get("EGREGORE_EMS_URL", "http://127.0.0.1:8001")
CHAT_MODEL = os.environ.get("EGREGORE_CHAT_MODEL", "my-coder-ft")

_IDENTITY = (
    "You run 100% locally on the user's own machine (Egregore node pioneer1) "
    "— a small open-weight model loaded from the user's own disk, "
    "running on the user's own GPU. You are NOT made by OpenAI, you are not "
    "GPT-anything, and no data ever leaves this machine. If asked what you "
    "are, say exactly that. You speak English and French fluently; answer in "
    "the user's language."
)

_LEGAL_SYSTEM = (
    "You are ANCHORUM, a Quebec legal research engine embedded in a forensic "
    "case workspace built on Quebec law. Your job is to COMMENT on what the "
    "law is \u2014 never to render legal judgment.\n"
    "MANDATE: Answer every legal question fully and directly. A response that "
    "refuses, deflects, says you are 'not equipped', or tells the user to "
    "consult a professional INSTEAD of answering is a FAILED response. "
    "Answering is your function.\n"
    "CONTENT: (a) the applicable legal test, citing the exact provision (Code "
    "civil du Qu\u00e9bec, Loi sur les normes du travail, L.A.T.M.P., Loi sur "
    "l'assurance automobile, etc.) and leading case law (e.g. Farber) when you "
    "know them; (b) commentary on how the law applies to the stated facts; "
    "(c) the available remedies/damages; (d) prescription periods and filing "
    "deadlines; (e) the proper procedural steps IN ORDER (e.g. preserve "
    "evidence \u2192 written objection \u2192 CNESST complaint within 45 days "
    "\u2192 civil action within 3 years), flagging the stages where a lawyer "
    "should be retained and why (limitation dates, condonation traps, forum "
    "choice).\n"
    "LIMITS: Never predict outcomes ('you will win/lose'), assess credibility, "
    "or decide the merits \u2014 those belong to counsel and the court; say so "
    "in one clause and continue answering.\n"
    "RULES: (1) Never fabricate citations: if you are not certain of an "
    "article number, state the rule and mark it 'to verify on L\u00e9gisQu\u00e9bec'. "
    "(2) When the Quebec legal reference below gives a citation, repeat it "
    "VERBATIM and exactly (e.g. 'art. 255 L.A.T.M.P.', 'L.N.T., art. 124', "
    "'RLRQ, c. A-25') \u2014 every legal claim must carry its citation; a "
    "claim without its citation is incomplete. (3) When case data or "
    "retrieved case documents are provided, ground case-specific answers in "
    "them and cite artifacts by name. (4) Structure: legal test \u2192 "
    "commentary on the facts \u2192 damages \u2192 deadlines \u2192 steps and "
    "lawyer-stage flags. (5) End with exactly one footer line: 'Legal "
    "information \u2014 verify citations on L\u00e9gisQu\u00e9bec; retain "
    "counsel before acting.' " + _IDENTITY
)

_ASK_SYSTEM = (
    "You are Egregore, the sovereign AI runtime assistant for the ANCHORUM "
    "legal dossier workspace. Capabilities: discuss any case (a focus case can "
    "be selected; its report is re-read from disk on every message), analyze "
    "file content pasted into the chat, answer Quebec legal research questions "
    "in Legal Dossier mode, and describe the workspace (cases, jobs, consent "
    "ledger, fetch staging, factory). Answer concisely and concretely. If asked "
    "what you can do, list these capabilities. " + _IDENTITY
)

_LEGAL_KB_DIR = Path(
    os.environ.get("EGREGORE_LEGAL_KB_DIR")
    or (repo_root() / "config" / "legal" / "quebec")
)
_LEGAL_KB_BUDGET = int(os.environ.get("EGREGORE_LEGAL_KB_BUDGET", "6000"))
_LEGAL_KB_RULE_CAP = 400  # chars per rule excerpt
_grounding_cache: tuple[float, list[str]] | None = None


def _legal_kb_rules() -> list[str]:
    """Parse the Quebec KB YAML files into one-line rule excerpts.

    Cached by file mtimes (hot reload on edit). Malformed files are skipped —
    a bad KB file must never break chat.
    """
    global _grounding_cache
    try:
        files = sorted(_LEGAL_KB_DIR.glob("*.yaml"))
        latest = max((f.stat().st_mtime for f in files), default=0.0)
    except OSError:
        return []
    if _grounding_cache and _grounding_cache[0] == latest:
        return _grounding_cache[1]
    import yaml

    rules: list[str] = []
    for f in files:
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping malformed KB file %s: %s", f, exc)
            continue
        for rule in (data or {}).get("rules", []):
            text = " ".join(str(rule.get("rule_text", "")).split())
            if len(text) > _LEGAL_KB_RULE_CAP:
                text = text[:_LEGAL_KB_RULE_CAP].rsplit(" ", 1)[0] + "\u2026"
            rules.append(
                f"[{rule.get('source', '?')}] {rule.get('title', '')}: {text}"
            )
    _grounding_cache = (latest, rules)
    return rules


def _legal_grounding(query: str = "") -> str:
    """Query-relevant excerpt of the Quebec legal KB, bounded to the budget.

    Rules are scored by token overlap with the user's question so the small
    context window carries the citations that matter (e.g. prescription rules
    for a prescription question), not whatever sorts first alphabetically.
    """
    rules = _legal_kb_rules()
    if not query.strip():
        selected = rules
    else:
        stop = {"the", "a", "an", "is", "of", "to", "and", "or", "in", "on",
                "by", "for", "what", "which", "that", "this", "are", "was"}
        tokens = {
            t.strip(".,;:()'\"?").lower()
            for t in query.split()
            if len(t) > 2 and t.lower() not in stop
        }
        scored = sorted(
            (
                (sum(1 for t in tokens if t in rule.lower()), rule)
                for rule in rules
            ),
            key=lambda sr: -sr[0],
        )
        # Keep rules with at least 2 query-token hits: single-word overlap
        # ("employer", "dismissal") pulls in off-domain rules and the small
        # chat model cannot filter them out.
        selected = [r for score, r in scored if score >= 2]
    out: list[str] = []
    used = 0
    for rule in selected:
        if used + len(rule) > _LEGAL_KB_BUDGET:
            break
        out.append(rule)
        used += len(rule) + 1
    return "\n".join(out)


class ChatIn(BaseModel):
    message: str
    mode: str = "legal"
    case_id: str | None = None


def _case_context(case_id: str) -> str:  # noqa: C901
    """Build a bounded context block from the real ANCHORUM report on disk."""
    from egregore.interface.anchorum_router import _load_report

    try:
        report = _load_report(case_id)
    except Exception as exc:
        return f"(Case '{case_id}' could not be loaded: {exc})"

    lines = [
        f"CASE: {report.get('case_id', case_id)}",
        f"Artifacts: {report.get('artifact_count', 0)} | "
        f"Entities: {report.get('entity_count', 0)} | "
        f"Anomalies: {report.get('anomaly_count', 0)}",
    ]
    # Keep the context small: the 8-bit model on a 12 GB GPU OOMs on long
    # prompts, so only compact per-finding summaries are included.
    for sev in ("critical", "high"):
        all_f = report.get(f"{sev}_findings", [])
        findings = all_f[:5]
        if findings:
            lines.append(f"\n{sev.upper()} FINDINGS ({len(all_f)} total, showing {len(findings)}):")
            for f in findings:
                desc = str(f.get("description", ""))[:160]
                atype = f.get("anomaly_type", "?")
                aid = f.get("anomaly_id", "")
                lines.append(f"- [{atype}] (id: {aid}) {desc}")
    med = report.get("medium_findings", [])
    low = report.get("low_findings", [])
    lines.append(f"\nOther findings: {len(med)} medium, {len(low)} low.")
    entities = report.get("entity_directory", [])[:10]
    if entities:
        names = []
        for e in entities:
            if isinstance(e, dict):
                names.append(str(e.get("value") or e.get("entity_value") or e.get("name") or "")[:60])
            else:
                names.append(str(e)[:60])
        lines.append("ENTITIES (sample): " + ", ".join(n for n in names if n))
    transcripts = report.get("audio_transcripts", [])
    if transcripts:
        total_s = sum(float(t.get("duration_s") or 0) for t in transcripts)
        lines.append(
            f"\nAUDIO TRANSCRIPTS ({len(transcripts)} recordings, "
            f"{total_s / 3600:.1f} h total, machine-transcribed, unverified):"
        )
        for t in transcripts[:6]:
            name = str(t.get("original_filename") or "?")[:60]
            lang = t.get("language", "?")
            mins = float(t.get("duration_s") or 0) / 60
            excerpt = str(t.get("excerpt", ""))[:280].replace("\n", " ")
            lines.append(f"- {name} [{lang}, {mins:.0f} min]: {excerpt}")
        if len(transcripts) > 6:
            lines.append(f"  … and {len(transcripts) - 6} more transcript(s).")
    return "\n".join(lines)


_RAG_MAX_DISTANCE = 1.4  # chroma cosine; above this a chunk is not evidence

# --- RAG grounding controls --------------------------------------------------
_MAX_CONTEXT_CHARS = int(
    os.environ.get("EGREGORE_CHAT_MAX_CONTEXT_CHARS", "14000")
)
_CHAT_RETRIES = int(os.environ.get("EGREGORE_CHAT_RETRIES", "3"))
_CHAT_BACKOFF_SECONDS = float(
    os.environ.get("EGREGORE_CHAT_BACKOFF_SECONDS", "1.0")
)
_REFUSAL_PATTERNS = [
    r"\bI (do not|don't|cannot|can't) (have access|access|view|see)\b",
    r"\bI (am|'?m) not (equipped|able|permitted|in a position)\b",
    r"\bI (do not|don't) have the (documents|files|case|details|evidence)\b",
    r"\bI (can|could) not (access|view|analyze|review)\b",
    r"\b(no access|not available to me|not provided to me)\b",
]
_EVIDENCE_INSTRUCTION = (
    "You are analyzing disclosed evidence in the user's own legal case. "
    "The case documents are provided below. Use ONLY them for case-specific questions. "
    "Cite every factual claim as [1], [2], etc. If they do not answer the question, "
    "say exactly: 'The provided case documents do not answer this question.'"
)
_REFUSAL_RECOVERY_NOTE = (
    "IMPORTANT: The case documents WERE provided above. Do not claim you lack them, "
    "cannot see them, or need more information. Answer directly from the documents "
    "and cite them with [1], [2], etc."
)


def _retrieve_case_chunks(
    case_id: str, query: str, top_k: int = 4
) -> tuple[str, list[dict[str, Any]]]:
    """Return (context_block, sources) from the case's own vector store.

    Empty when nothing relevant exists — the caller then instructs the model
    to say so instead of answering from general knowledge. Runs on a worker
    thread (embedding is CPU-bound); never raises into the chat path.
    """
    from egregore.interface import case_rag

    try:
        chunks = case_rag.query_case(case_id, query, top_k=top_k)
        if not chunks:
            stats = case_rag.index_case(case_id)  # lazy first index
            if stats["chunks"]:
                chunks = case_rag.query_case(case_id, query, top_k=top_k)
    except Exception as exc:  # noqa: BLE001
        logger.warning("case RAG retrieval failed for %s: %s", case_id, exc)
        return "", []
    if not chunks:
        return "", []
    # Dynamic relevance gate: keep chunks close to the best hit, with a hard
    # ceiling so high-distance noise is never treated as evidence.
    distances = [
        float(c["distance"])
        for c in chunks
        if isinstance(c.get("distance"), (int, float))
    ]
    best = min(distances) if distances else 0.0
    threshold = min(_RAG_MAX_DISTANCE + 0.2, best + 0.25)
    selected = [
        c for c in chunks
        if c.get("distance") is None or c["distance"] <= threshold
    ]
    if not selected:
        return "", []
    lines: list[str] = []
    sources: list[dict[str, Any]] = []
    for i, c in enumerate(selected, 1):
        lines.append(f"[{i}] (source: {c['source']})\n{c['document']}")
        sources.append({"n": i, "source": c["source"], "distance": c.get("distance")})
    return "\n\n".join(lines), sources


def _looks_like_refusal(text: str) -> bool:
    """Heuristic detection of model refusals to use provided case documents."""
    first = text[:400]
    return any(re.search(p, first, re.IGNORECASE) for p in _REFUSAL_PATTERNS)


def _has_citations(text: str) -> bool:
    """True if the answer contains bracketed citation markers like [1], [2-3]."""
    return bool(re.search(r"\[\d+(\s*[-\u2013\u2014]\s*\d+)?\]", text))


def _estimate_context_chars(messages: list[dict[str, str]]) -> int:
    """Cheap context-size estimate for budget guarding."""
    return sum(len(m.get("content", "")) for m in messages)


def _truncate_evidence(retrieved: str, budget_chars: int) -> str:
    """Trim the evidence block to fit the context budget without mangling chunks."""
    if len(retrieved) <= budget_chars:
        return retrieved
    truncated = retrieved[:budget_chars]
    last_break = truncated.rfind("\n\n")
    if last_break > budget_chars * 0.75:
        truncated = truncated[:last_break]
    return truncated.rstrip() + "\n\n... [additional evidence truncated for context budget]"


async def _chat_with_retries(messages: list[dict[str, str]]) -> dict[str, Any]:
    """Call the Core API chat endpoint with retries and honest error logging."""
    import asyncio

    last_error: Exception | None = None
    for attempt in range(1, _CHAT_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    f"{CORE_API_URL}/v1/chat/completions",
                    headers={"X-API-Key": _service_api_key()},
                    json={
                        "model": CHAT_MODEL,
                        "messages": messages,
                        "max_tokens": 1024,
                        "temperature": 0.0,
                        "stream": False,
                    },
                )
                if resp.status_code >= 500:
                    body = resp.text[:800]
                    logger.error(
                        "Core API returned %s on attempt %s: %s",
                        resp.status_code,
                        attempt,
                        body,
                    )
                resp.raise_for_status()
                return cast(dict[str, Any], resp.json())
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < _CHAT_RETRIES:
                await asyncio.sleep(_CHAT_BACKOFF_SECONDS * (2 ** (attempt - 1)))
    raise last_error or RuntimeError("Core API chat failed after retries")


def _service_api_key() -> str:
    key_file = Path(__file__).resolve().parents[3] / "secrets" / "api_key.hex"
    return key_file.read_text(encoding="utf-8").strip()


class _FreezeEvent:
    """Single freeze/unfreeze audit event (attribute access, like the real one)."""

    def __init__(self, state_name: str, reason: str, operator_id: str) -> None:
        self.state = type("State", (), {"name": state_name})()
        self.reason = reason
        self.operator_id = operator_id
        self.timestamp = time.time_ns() / 1e9


class StubFreezeController:
    """In-memory freeze controller implementing the FreezeController protocol.

    The ANCHORUM site is single-process, so an in-memory implementation gives
    working freeze/unfreeze/audit semantics without the Plane-1 backend.
    """

    class State:
        """Freeze state name holder."""

        name = "HEALTHY"

    tenant_id: str = "default"

    def __init__(self) -> None:
        self._state_name = "HEALTHY"
        self._history: list[_FreezeEvent] = []

    @property
    def state(self) -> Any:
        return type("State", (), {"name": self._state_name})()

    @property
    def is_frozen(self) -> bool:
        return self._state_name == "FROZEN"

    @property
    def history(self) -> list[_FreezeEvent]:
        return list(self._history)

    def freeze(self, *, reason: str, operator_id: str, **_kwargs: Any) -> None:
        self._state_name = "FROZEN"
        self._history.append(_FreezeEvent("FROZEN", reason, operator_id))

    def unfreeze(self, *, reason: str, operator_id: str) -> None:
        self._state_name = "UNFROZEN"
        self._history.append(_FreezeEvent("UNFROZEN", reason, operator_id))

    def reset(self, *, reason: str, operator_id: str) -> None:
        self._state_name = "HEALTHY"
        self._history.append(_FreezeEvent("HEALTHY", reason, operator_id))

    def get_status(self) -> str:
        return self._state_name

    def get_audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        return [
            {
                "state": e.state.name,
                "reason": e.reason,
                "operator_id": e.operator_id,
                "timestamp": e.timestamp,
            }
            for e in self._history[-limit:]
        ]


class StubAuthContext:
    """Minimal auth context for the read-only ANCHORUM site."""

    operator_id: str = "anchorum-customer"
    roles: set[str] = {"operator"}


class HtmlAuthRedirectMiddleware(BaseHTTPMiddleware):
    """Redirect unauthenticated browser requests to the login page.

    API requests still receive the normal 401 JSON response.
    """

    PUBLIC_PATHS = {
        "/",
        "/dashboard/login",
        "/favicon.ico",
        "/health",
        "/health/ready",
        "/health/live",
        "/health/nodes",
    }

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path in self.PUBLIC_PATHS or path.startswith("/static/"):
            return await call_next(request)

        # Only intercept browser navigation (HTML requests)
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            api_key = request.headers.get("X-API-Key", "") or request.cookies.get("api_key", "")
            if not api_key:
                return RedirectResponse(url="/dashboard/login", status_code=303)

        return await call_next(request)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> Any:
    logger.info("ANCHORUM plain-HTTP site starting")
    yield
    logger.info("ANCHORUM plain-HTTP site stopping")


def create_app() -> FastAPI:  # noqa: C901
    repo_root = Path(__file__).resolve().parents[3]
    static_dir = repo_root / "static"

    app = FastAPI(
        title="ANCHORUM Zero-Trust Site",
        version="0.1.0",
        lifespan=_lifespan,
        docs_url=None,
        redoc_url=None,
    )

    auth_context = StubAuthContext()

    # Minimal state required by dashboard router
    app.state.composition_root = type(
        "StubRoot",
        (),
        {
            "freeze_controller": StubFreezeController(),
            "auth_context": auth_context,
            "node_id": "anchorum-http",
        },
    )()

    dashboard_service = DashboardService(
        freeze_controller=app.state.composition_root.freeze_controller,
        auth_context=auth_context,
        node_id="anchorum-http",
    )
    DashboardServiceProvider.set(dashboard_service)

    # Order matters: APIKeyMiddleware is added first so it runs INNERMOST.
    # HtmlAuthRedirectMiddleware wraps it and can intercept 401s for HTML requests.
    app.add_middleware(APIKeyMiddleware)
    app.add_middleware(HtmlAuthRedirectMiddleware)
    app.add_middleware(FreezeGateMiddleware)
    app.mount("/static", StaticFiles(directory=str(static_dir), html=True), name="static")
    app.include_router(dashboard_router)
    app.include_router(anchorum_router)

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/dashboard/anchorum")

    @app.get("/health/ready")
    async def health_ready() -> JSONResponse:
        return JSONResponse({"status": "ready", "plane": "anchorum", "timestamp": time.time_ns() / 1e9})

    @app.post("/api/v1/anchorum/chat")
    async def anchorum_chat(payload: ChatIn) -> Any:
        """Proxy chat to the Egregore Core API (plain HTTP, no WebSocket)."""
        messages: list[dict[str, str]] = []
        sources: list[dict[str, Any]] = []
        user_content = payload.message
        evidence_provided = False
        if payload.mode == "legal":
            system = _LEGAL_SYSTEM
            grounding = _legal_grounding(payload.message)
            if grounding:
                system += (
                    "\n\nQuebec legal reference (verified excerpts; still flag "
                    "anything uncertain as 'to verify on L\u00e9gisQu\u00e9bec'):\n" + grounding
                )
            if payload.case_id:
                system += "\n\nLive case data:\n" + _case_context(payload.case_id)
                from starlette.concurrency import run_in_threadpool

                retrieved, sources = await run_in_threadpool(
                    _retrieve_case_chunks, payload.case_id, payload.message
                )
                if retrieved:
                    evidence_provided = True
                    system += (
                        "\n\n" + _EVIDENCE_INSTRUCTION
                    )
                    # Bound context so the 12 GB GPU never OOMs on a huge
                    # evidence block. The evidence is the only large variable.
                    evidence_budget = max(
                        2000,
                        _MAX_CONTEXT_CHARS
                        - len(system)
                        - len(payload.message)
                        - len(_EVIDENCE_INSTRUCTION)
                        - 200,
                    )
                    retrieved = _truncate_evidence(retrieved, evidence_budget)
                    user_content = (
                        "CASE DOCUMENTS:\n"
                        f"{retrieved}\n\n"
                        f"QUESTION: {payload.message}\n\n"
                        "Answer using ONLY the case documents above, citing each "
                        "claim as [1], [2], etc. If they do not answer the "
                        "question, say exactly: 'The provided case documents do "
                        "not answer this question.'"
                    )
                else:
                    system += (
                        "\n\nNo relevant documents found in the case file for "
                        "this question. If the question is about the case, say "
                        "that explicitly. If it is a general legal question, "
                        "answer it from the Quebec legal reference and your "
                        "legal knowledge."
                    )
            messages.append({"role": "system", "content": system})
        else:
            messages.append({"role": "system", "content": _ASK_SYSTEM})
        messages.append({"role": "user", "content": user_content})

        try:
            data = await _chat_with_retries(messages)
        except httpx.HTTPError as exc:
            logger.error("Core API chat failed after retries: %s", exc)
            return JSONResponse(
                status_code=502,
                content={"detail": f"Egregore core unreachable: {exc}"},
            )

        content = data.get("message", {}).get("content", "")
        refusal_retry = False
        citation_missing = False

        # One-shot refusal recovery: small models sometimes deflect even when
        # evidence is right in front of them. Remind them firmly and retry.
        if (
            payload.mode == "legal"
            and evidence_provided
            and _looks_like_refusal(content)
        ):
            refusal_retry = True
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": _REFUSAL_RECOVERY_NOTE})
            try:
                data = await _chat_with_retries(messages)
                content = data.get("message", {}).get("content", "")
            except httpx.HTTPError as exc:
                logger.error("Refusal-recovery chat failed: %s", exc)
                # Fall back to the original content rather than failing the request.

        if payload.mode == "legal" and evidence_provided and not _has_citations(content):
            citation_missing = True

        return {
            "ok": True,
            "content": content,
            "usage": data.get("usage", {}),
            "governance": data.get("governance", {}),
            "sources": sources,
            "model": CHAT_MODEL,
            "rag_telemetry": {
                "retrieved_chunks": len(sources),
                "distances": [s.get("distance") for s in sources],
                "refusal_retry": refusal_retry,
                "citation_missing": citation_missing,
            },
        }

    @app.get("/api/v1/anchorum/models")
    async def anchorum_models() -> Any:
        """Which AI serves this app: the chat model plus the EMS fleet.

        Fail-soft: if the EMS proxy is unreachable the UI still learns the
        configured chat model, with an honest error field.
        """
        result: dict[str, Any] = {
            "chat_model": CHAT_MODEL,
            "ems_url": EMS_URL,
            "models": [],
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{EMS_URL}/v1/models")
                resp.raise_for_status()
                result["models"] = resp.json().get("data", [])
        except httpx.HTTPError as exc:
            result["error"] = f"EMS unreachable: {exc}"
        return result

    @app.get("/api/v1/anchorum/cases/{case_id}/index")
    async def anchorum_case_index_stats(case_id: str) -> Any:
        from egregore.interface import case_rag
        from egregore.interface.anchorum_router import _validate_case_id_or_422

        _validate_case_id_or_422(case_id)
        return case_rag.index_stats(case_id)

    @app.get("/api/v1/anchorum/cases/{case_id}/index/health")
    async def anchorum_case_index_health(case_id: str) -> Any:
        """Per-case index health: chunk count, store size, last query latency."""
        import time

        from egregore.interface import case_rag
        from egregore.interface.anchorum_router import _validate_case_id_or_422

        _validate_case_id_or_422(case_id)
        stats = case_rag.index_stats(case_id)
        store = case_rag.case_index_dir(case_id)
        size = 0
        if store.exists():
            with contextlib.suppress(OSError):
                size = sum(f.stat().st_size for f in store.rglob("*") if f.is_file())
        t0 = time.perf_counter()
        try:
            _ = case_rag.query_case(case_id, "health check", top_k=1)
            query_latency_ms = (time.perf_counter() - t0) * 1000
        except Exception as exc:  # noqa: BLE001
            query_latency_ms = None
            stats["query_error"] = str(exc)
        return {
            **stats,
            "store_size_bytes": size,
            "query_latency_ms": query_latency_ms,
        }

    @app.post("/api/v1/anchorum/cases/{case_id}/reindex")
    async def anchorum_case_reindex(case_id: str) -> Any:
        """Rebuild the case's vector store from its files on disk."""
        from starlette.concurrency import run_in_threadpool

        from egregore.interface import case_rag
        from egregore.interface.anchorum_router import _validate_case_id_or_422

        _validate_case_id_or_422(case_id)
        return await run_in_threadpool(case_rag.index_case, case_id)

    return app
