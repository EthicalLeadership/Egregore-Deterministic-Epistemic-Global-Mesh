from __future__ import annotations

# uvicorn entrypoint
#
# IMPORTANT:
# - In this repo's execution environment, FastAPI may not be installed.
# - Therefore, this module must be safe to import even when FastAPI is missing.
try:
    from egregore.http_api.http.app import create_app

    app = create_app()
except Exception:  # pragma: no cover
    # If FastAPI isn't installed or router wiring fails, keep this module import-safe.
    app = None
