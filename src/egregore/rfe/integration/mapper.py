"""
Bridge between orchestration suite's JobRequest and runtime's WorkUnit (DT1).
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4


def job_request_to_work_unit(job: dict) -> dict:
    """
    Transforms a JobRequest (from the orchestration suite) into a WorkUnit
    compatible with the DT1 runtime.
    """
    return {
        "unit_id": job.get("job_id", str(uuid4())),
        "task": job.get("task_spec", {}).get("action", "unknown"),
        "payload": job.get("task_spec", {}).get("parameters", {}),
        "deadline": job.get("deadline", (datetime.utcnow().timestamp() + 3600)),
        "priority": job.get("priority", 0.5),
        "source": "orchestration_suite",
        "provenance": {
            "original_request": job,
            "mapped_at": datetime.utcnow().isoformat(),
        },
    }


def work_unit_to_job_response(unit_result: dict) -> dict:
    """
    Transforms a WorkUnit result back into a JobResponse for the orchestrator.
    """
    return {
        "job_id": unit_result.get("unit_id"),
        "status": unit_result.get("status", "completed"),
        "output": unit_result.get("output", {}),
        "provenance": unit_result.get("provenance", {}),
    }
