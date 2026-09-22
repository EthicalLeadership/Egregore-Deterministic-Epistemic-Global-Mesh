"""Typed argument schemas for the ANCHORUM tool adapter.

Schemas validate arguments before any network call and never leak internal
validation exceptions as raw error messages.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CASE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_case_id(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("case_id must be a non-empty string")
    if len(value) > 128:
        raise ValueError("case_id must be 128 characters or fewer")
    if not _CASE_ID_RE.match(value):
        raise ValueError("case_id contains invalid characters")
    return value


class ListCasesArgs(BaseModel):
    """Arguments for anchorum.list_cases."""

    model_config = ConfigDict(extra="forbid")


class GetCaseReportArgs(BaseModel):
    """Arguments for anchorum.get_case_report."""

    case_id: str = Field(..., min_length=1, max_length=128)
    model_config = ConfigDict(extra="forbid")

    @field_validator("case_id")
    @classmethod
    def _check_case_id(cls, value: str) -> str:
        return _validate_case_id(value)


class GetCaseSummaryArgs(BaseModel):
    """Arguments for anchorum.get_case_summary."""

    case_id: str = Field(..., min_length=1, max_length=128)
    model_config = ConfigDict(extra="forbid")

    @field_validator("case_id")
    @classmethod
    def _check_case_id(cls, value: str) -> str:
        return _validate_case_id(value)


class GetTimelineArgs(BaseModel):
    """Arguments for anchorum.get_timeline."""

    case_id: str = Field(..., min_length=1, max_length=128)
    model_config = ConfigDict(extra="forbid")

    @field_validator("case_id")
    @classmethod
    def _check_case_id(cls, value: str) -> str:
        return _validate_case_id(value)


class GetAnomaliesArgs(BaseModel):
    """Arguments for anchorum.get_anomalies."""

    case_id: str = Field(..., min_length=1, max_length=128)
    model_config = ConfigDict(extra="forbid")

    @field_validator("case_id")
    @classmethod
    def _check_case_id(cls, value: str) -> str:
        return _validate_case_id(value)


class JobStatusArgs(BaseModel):
    """Arguments for anchorum.job_status."""

    job_id: str = Field(..., min_length=1, max_length=128)
    model_config = ConfigDict(extra="forbid")


class BuildTimelineArgs(BaseModel):
    """Arguments for anchorum.build_timeline (write-gated)."""

    case_id: str = Field(..., min_length=1, max_length=128)
    request_id: str = Field(..., min_length=1, max_length=128)
    force: bool = False
    model_config = ConfigDict(extra="forbid")

    @field_validator("case_id")
    @classmethod
    def _check_case_id(cls, value: str) -> str:
        return _validate_case_id(value)


class ReindexCaseArgs(BaseModel):
    """Arguments for anchorum.reindex_case (write-gated)."""

    case_id: str = Field(..., min_length=1, max_length=128)
    request_id: str = Field(..., min_length=1, max_length=128)
    extra_dirs: list[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")

    @field_validator("case_id")
    @classmethod
    def _check_case_id(cls, value: str) -> str:
        return _validate_case_id(value)

    @field_validator("extra_dirs")
    @classmethod
    def _check_extra_dirs(cls, value: list[str]) -> list[str]:
        for d in value:
            if not isinstance(d, str) or ".." in d:
                raise ValueError(f"invalid extra_dir: {d}")
        return value
