"""Egregore-side adapter that treats ANCHORUM as a governed tool provider.

The adapter is the only boundary Egregore code may use to reach ANCHORUM. It:

- pins TLS to the ANCHORUM loopback certificate (or system CAs when absent),
- authenticates with a dedicated Egregore service key,
- translates every outcome into a stable envelope with a closed error taxonomy,
- never leaks raw HTTP statuses, Python exceptions, or internal paths,
- preserves a single request ID end-to-end and into ANCHORUM audit records,
- gates writes behind ``ANCHORUM_ADAPTER_WRITES_ENABLED=1``.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from egregore.tools.anchorum.schemas import (
    BuildTimelineArgs,
    GetAnomaliesArgs,
    GetCaseReportArgs,
    GetCaseSummaryArgs,
    GetTimelineArgs,
    JobStatusArgs,
    ListCasesArgs,
    ReindexCaseArgs,
)

logger = logging.getLogger("egregore.tools.anchorum")

_DEFAULT_BASE_URL = os.environ.get("ANCHORUM_ADAPTER_URL", "https://127.0.0.1:8444")
_DEFAULT_TLS_CERT = os.environ.get(
    "ANCHORUM_ADAPTER_TLS_CERT", "/etc/egregore/ssl/anchorum.crt"
)
_DEFAULT_KEY_PATH = os.environ.get("ANCHORUM_EGREGORE_KEY_PATH", "")
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class AnchorumError(Exception):
    """Raised when the adapter itself cannot build a request or read its key."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "internal",
        request_id: str | None = None,
        tool: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.request_id = request_id or ""
        self.tool = tool or ""


class AnchorumResult(BaseModel):
    """Stable envelope returned by every adapter tool invocation."""

    version: str = "1.0"
    ok: bool
    tool: str
    request_id: str
    principal: str
    case_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    error_code: str | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)
    elapsed_ms: float = 0.0

    model_config = ConfigDict(extra="forbid")


class AnchorumClient:
    """Read-mostly HTTP client for the ANCHORUM forensic API.

    Args:
        base_url: ANCHORUM HTTPS API root, e.g. ``https://127.0.0.1:8444``.
        api_key: Egregore service API key. If omitted, read from
            ``secrets/egregore.key`` relative to the repository root.
        tls_cert: Path to the pinned ANCHORUM certificate. If the file does
            not exist, system CAs are used and a warning is logged.
        principal: Identity recorded in ANCHORUM audit records.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        tls_cert: str | Path | None = None,
        principal: str = "egregore",
    ) -> None:
        self.base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self.principal = principal
        self._api_key = (api_key or self._load_service_key()).strip()
        self._verify = self._resolve_tls(tls_cert)
        self._writes_enabled = os.environ.get("ANCHORUM_ADAPTER_WRITES_ENABLED", "0") == "1"

    @staticmethod
    def _repo_root() -> Path:
        # client.py is at src/egregore/tools/anchorum/client.py
        return Path(__file__).resolve().parents[4]

    @classmethod
    def _load_service_key(cls) -> str:
        path = Path(_DEFAULT_KEY_PATH) if _DEFAULT_KEY_PATH else cls._repo_root() / "secrets" / "egregore.key"
        if not path.exists():
            raise AnchorumError(
                "Egregore service key not found",
                code="internal",
            )
        return path.read_text(encoding="utf-8").strip()

    def _resolve_tls(self, tls_cert: str | Path | None) -> str | bool:
        cert = Path(tls_cert) if tls_cert else Path(_DEFAULT_TLS_CERT)
        if cert.exists():
            logger.debug("Pinning ANCHORUM TLS to %s", cert)
            return str(cert)
        logger.warning(
            "Pinned ANCHORUM certificate not found at %s; falling back to system CAs",
            cert,
        )
        return True

    def _new_request_id(self) -> str:
        try:
            from ulid import ULID  # type: ignore[import-untyped]
            return str(ULID())
        except Exception:
            return str(uuid.uuid4())

    def _headers(self, request_id: str) -> dict[str, str]:
        return {
            "X-API-Key": self._api_key,
            "X-Request-ID": request_id,
            "X-Egregore-Principal": self.principal,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _fail(
        self,
        tool: str,
        request_id: str,
        message: str,
        code: str,
        case_id: str | None = None,
        elapsed_ms: float = 0.0,
    ) -> AnchorumResult:
        logger.warning("ANCHORUM adapter failure [%s] %s: %s", tool, code, message)
        return AnchorumResult(
            ok=False,
            tool=tool,
            request_id=request_id,
            principal=self.principal,
            case_id=case_id,
            error=message,
            error_code=code,
            elapsed_ms=elapsed_ms,
        )

    def _map_http_error(self, status: int, detail: str, path: str) -> tuple[str, str]:
        """Map an HTTP status to a closed error code and safe message."""
        if status == 400:
            return "invalid_argument", "Invalid request"
        if status == 401:
            return "unauthorized", "Authentication failed"
        if status == 403:
            return "forbidden", "Access denied"
        if status == 404:
            if "/cases/" in path:
                return "case_not_found", "Case not found"
            return "not_found", "Requested resource not found"
        if status == 422:
            return "invalid_argument", detail or "Validation failed"
        if status == 423:
            return "frozen", "ANCHORUM is frozen"
        if status == 429:
            return "rate_limited", "Rate limit exceeded"
        if status == 502 or status == 503:
            return "upstream_error", "ANCHORUM upstream unavailable"
        if status == 504:
            return "upstream_timeout", "ANCHORUM upstream timeout"
        if status >= 500:
            return "upstream_error", "ANCHORUM upstream error"
        return "internal", "Unexpected ANCHORUM response"

    def _request(
        self,
        tool: str,
        method: str,
        path: str,
        *,
        case_id: str | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> AnchorumResult:
        """Execute one HTTP request and return a governed envelope."""
        rid = request_id or self._new_request_id()
        start = time.perf_counter()
        url = self._url(path)

        try:
            with httpx.Client(
                verify=self._verify,
                timeout=_DEFAULT_TIMEOUT,
            ) as client:
                resp = client.request(
                    method,
                    url,
                    headers=self._headers(rid),
                    json=json_body,
                    params=params,
                )
        except httpx.TimeoutException as exc:
            elapsed = (time.perf_counter() - start) * 1000
            logger.error("ANCHORUM request timeout [%s] %s: %s", tool, url, exc)
            return self._fail(
                tool, rid, "ANCHORUM request timed out", "upstream_timeout",
                case_id=case_id, elapsed_ms=elapsed,
            )
        except httpx.NetworkError as exc:
            elapsed = (time.perf_counter() - start) * 1000
            logger.error("ANCHORUM network error [%s] %s: %s", tool, url, exc)
            return self._fail(
                tool, rid, "Cannot reach ANCHORUM", "upstream_error",
                case_id=case_id, elapsed_ms=elapsed,
            )
        except httpx.HTTPError as exc:
            elapsed = (time.perf_counter() - start) * 1000
            logger.error("ANCHORUM HTTP error [%s] %s: %s", tool, url, exc)
            return self._fail(
                tool, rid, "ANCHORUM transport error", "upstream_error",
                case_id=case_id, elapsed_ms=elapsed,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = (time.perf_counter() - start) * 1000
            logger.exception("ANCHORUM unexpected transport error [%s]", tool)
            return self._fail(
                tool, rid, "Adapter transport error", "internal",
                case_id=case_id, elapsed_ms=elapsed,
            )

        elapsed = (time.perf_counter() - start) * 1000

        if resp.status_code >= 400:
            code, message = self._map_http_error(
                resp.status_code,
                (resp.text or "")[:200],
                path,
            )
            return self._fail(
                tool, rid, message, code,
                case_id=case_id, elapsed_ms=elapsed,
            )

        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("ANCHORUM non-JSON response [%s]: %s", tool, exc)
            return self._fail(
                tool, rid, "Invalid response from ANCHORUM", "upstream_error",
                case_id=case_id, elapsed_ms=elapsed,
            )

        return AnchorumResult(
            ok=True,
            tool=tool,
            request_id=rid,
            principal=self.principal,
            case_id=case_id,
            data=payload if isinstance(payload, dict) else {"value": payload},
            elapsed_ms=elapsed,
        )

    # -----------------------------------------------------------------------
    # Read-only tools
    # -----------------------------------------------------------------------

    def list_cases(self, **_kwargs: Any) -> AnchorumResult:
        """Return the list of case IDs known to ANCHORUM."""
        ListCasesArgs.model_validate(_kwargs or {})
        return self._request("anchorum.list_cases", "GET", "/api/v1/anchorum/cases")

    def get_case_report(self, case_id: str, **_kwargs: Any) -> AnchorumResult:
        """Return the full investigation report for a case."""
        args = GetCaseReportArgs.model_validate({"case_id": case_id, **_kwargs})
        return self._request(
            "anchorum.get_case_report",
            "GET",
            f"/api/v1/anchorum/cases/{args.case_id}",
            case_id=args.case_id,
        )

    def get_case_summary(self, case_id: str, **_kwargs: Any) -> AnchorumResult:
        """Return a compact summary for a case."""
        args = GetCaseSummaryArgs.model_validate({"case_id": case_id, **_kwargs})
        return self._request(
            "anchorum.get_case_summary",
            "GET",
            f"/api/v1/anchorum/cases/{args.case_id}/summary",
            case_id=args.case_id,
        )

    def get_timeline(self, case_id: str, **_kwargs: Any) -> AnchorumResult:
        """Return the master timeline for a case."""
        args = GetTimelineArgs.model_validate({"case_id": case_id, **_kwargs})
        result = self._request(
            "anchorum.get_timeline",
            "GET",
            f"/api/v1/anchorum/cases/{args.case_id}/timeline",
            case_id=args.case_id,
        )
        if result.ok:
            timeline = result.data.get("timeline") or []
            result.sources = [
                {"event_id": ev.get("id"), "source": ev.get("source")}
                for ev in timeline
                if isinstance(ev, dict) and ev.get("source")
            ]
        return result

    def get_anomalies(self, case_id: str, **_kwargs: Any) -> AnchorumResult:
        """Return anomaly findings for a case, grouped by severity."""
        args = GetAnomaliesArgs.model_validate({"case_id": case_id, **_kwargs})
        result = self._request(
            "anchorum.get_anomalies",
            "GET",
            f"/api/v1/anchorum/cases/{args.case_id}/anomalies",
            case_id=args.case_id,
        )
        if result.ok:
            sources: list[dict[str, Any]] = []
            for bucket in ("critical", "high", "medium", "low", "info"):
                for finding in result.data.get(bucket) or []:
                    src = finding.get("source") or finding.get("artifact_id")
                    if src:
                        sources.append(
                            {
                                "anomaly_id": finding.get("anomaly_id"),
                                "source": src,
                                "severity": bucket,
                            }
                        )
            result.sources = sources
        return result

    def job_status(self, job_id: str, **_kwargs: Any) -> AnchorumResult:
        """Return the status of an ANCHORUM background job."""
        args = JobStatusArgs.model_validate({"job_id": job_id, **_kwargs})
        return self._request(
            "anchorum.job_status",
            "GET",
            f"/api/v1/anchorum/jobs/{args.job_id}",
        )

    def list_jobs(self, **_kwargs: Any) -> AnchorumResult:
        """Return all tracked ANCHORUM background jobs."""
        ListCasesArgs.model_validate(_kwargs or {})
        return self._request("anchorum.list_jobs", "GET", "/api/v1/anchorum/jobs")

    # -----------------------------------------------------------------------
    # Write-gated tools (default disabled)
    # -----------------------------------------------------------------------

    def _reject_write(self, tool: str, request_id: str) -> AnchorumResult:
        return self._fail(
            tool,
            request_id,
            "Write tools are disabled. Set ANCHORUM_ADAPTER_WRITES_ENABLED=1 to enable.",
            "forbidden",
        )

    def build_timeline(
        self,
        case_id: str,
        request_id: str,
        force: bool = False,
        **_kwargs: Any,
    ) -> AnchorumResult:
        """Queue a timeline build job for a case (write-gated)."""
        if not self._writes_enabled:
            return self._reject_write("anchorum.build_timeline", request_id)
        args = BuildTimelineArgs.model_validate(
            {"case_id": case_id, "request_id": request_id, "force": force, **_kwargs}
        )
        # ANCHORUM has no dedicated "build_timeline" endpoint yet; queue via batch.
        return self._request(
            "anchorum.build_timeline",
            "POST",
            "/api/v1/anchorum/batch",
            case_id=args.case_id,
            json_body={
                "input_path": str(self._repo_root() / "audit" / "evidence"),
                "case_id": args.case_id,
                "operator": self.principal,
            },
            request_id=args.request_id,
        )

    def reindex_case(
        self,
        case_id: str,
        request_id: str,
        extra_dirs: list[str] | None = None,
        **_kwargs: Any,
    ) -> AnchorumResult:
        """Queue a case re-index job for RAG (write-gated)."""
        if not self._writes_enabled:
            return self._reject_write("anchorum.reindex_case", request_id)
        args = ReindexCaseArgs.model_validate(
            {
                "case_id": case_id,
                "request_id": request_id,
                "extra_dirs": extra_dirs or [],
                **_kwargs,
            }
        )
        return self._request(
            "anchorum.reindex_case",
            "POST",
            f"/api/v1/anchorum/cases/{args.case_id}/rag/index",
            case_id=args.case_id,
            json_body={"extra_dirs": args.extra_dirs},
            request_id=args.request_id,
        )


# ---------------------------------------------------------------------------
# OpenAI-compatible tool definitions for the Egregore agent runtime
# ---------------------------------------------------------------------------

ANCHORUM_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "anchorum.list_cases",
            "description": "List all case IDs available in the ANCHORUM workspace.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "anchorum.get_case_report",
            "description": "Return the full investigation report for a specific case.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {
                        "type": "string",
                        "description": "ANCHORUM case identifier, e.g. MOLSON-2026",
                    },
                },
                "required": ["case_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "anchorum.get_case_summary",
            "description": "Return a compact summary (artifact/entity/anomaly counts) for a case.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {
                        "type": "string",
                        "description": "ANCHORUM case identifier, e.g. MOLSON-2026",
                    },
                },
                "required": ["case_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "anchorum.get_timeline",
            "description": "Return the master timeline of events for a case. Use this for any timeline, chronology, or sequence question.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {
                        "type": "string",
                        "description": "ANCHORUM case identifier, e.g. MOLSON-2026",
                    },
                },
                "required": ["case_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "anchorum.get_anomalies",
            "description": "Return anomaly findings for a case, grouped by severity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {
                        "type": "string",
                        "description": "ANCHORUM case identifier, e.g. MOLSON-2026",
                    },
                },
                "required": ["case_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "anchorum.job_status",
            "description": "Return the status and result of an ANCHORUM background job.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "ANCHORUM job UUID returned by a write tool.",
                    },
                },
                "required": ["job_id"],
                "additionalProperties": False,
            },
        },
    },
]

if os.environ.get("ANCHORUM_ADAPTER_WRITES_ENABLED", "0") == "1":
    ANCHORUM_TOOLS.extend(
        [
            {
                "type": "function",
                "function": {
                    "name": "anchorum.build_timeline",
                    "description": "Queue a timeline build job for a case.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "case_id": {
                                "type": "string",
                                "description": "ANCHORUM case identifier",
                            },
                            "request_id": {
                                "type": "string",
                                "description": "ULID request identifier for idempotency and audit.",
                            },
                            "force": {
                                "type": "boolean",
                                "description": "Whether to overwrite an existing timeline.",
                            },
                        },
                        "required": ["case_id", "request_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "anchorum.reindex_case",
                    "description": "Queue a case RAG re-index job.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "case_id": {
                                "type": "string",
                                "description": "ANCHORUM case identifier",
                            },
                            "request_id": {
                                "type": "string",
                                "description": "ULID request identifier for idempotency and audit.",
                            },
                            "extra_dirs": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Additional evidence directories to index.",
                            },
                        },
                        "required": ["case_id", "request_id"],
                        "additionalProperties": False,
                    },
                },
            },
        ]
    )
