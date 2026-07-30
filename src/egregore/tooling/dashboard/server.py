# epistemic marker: provenance / auditability
"""Interface Synod Dashboard — read-only governance window for sandbox output.

The dashboard consumes the cryptographically signed aggregate report produced by
``scripts/pipeline_sandbox.py`` and renders it for the Human Assembly, AI
Conclave, and Interface Synod. It does not mutate Plane-1 state.

Environment variables:
  EGREGORE_SANDBOX_OUTPUT  Directory containing aggregate_report.json
                            (default: sandbox_outputs).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Egregore Interface Synod Dashboard")

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _report_path() -> Path:
    """Return the aggregate report path, respecting EGREGORE_SANDBOX_OUTPUT."""
    out_dir = Path(
        os.environ.get("EGREGORE_SANDBOX_OUTPUT", "sandbox_outputs")
    ).resolve()
    return out_dir / "aggregate_report.json"


def _load_report() -> dict[str, Any]:
    """Load the aggregate sandbox report."""
    path = _report_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"Aggregate report not found at {path}. Run 'make sandbox' first."
        )
    with path.open(encoding="utf-8") as f:
        return json.load(f)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Serve the single-page dashboard."""
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/api/report")
async def get_report() -> dict[str, Any]:
    """Return the full enriched aggregate report as JSON."""
    try:
        return _load_report()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/modules/{module_id}")
async def get_module(module_id: str) -> dict[str, Any]:
    """Return a single module's record from the aggregate report.

    ``module_id`` may be the fully-qualified form (``egregore.shared``) or the
    short name (``shared``).
    """
    try:
        report = _load_report()
        for mod in report.get("module_results", []):
            if mod.get("module_id") == module_id or mod.get("name") == module_id:
                return mod
        raise HTTPException(status_code=404, detail="Module not found")
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
