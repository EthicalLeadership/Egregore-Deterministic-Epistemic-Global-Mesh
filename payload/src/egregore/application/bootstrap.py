"""
EGREGORE LAW: Bootstrap
Entry point for production deployment. Reads env, builds container, returns app.
"""

from __future__ import annotations

from egregore.application.container import EgregoreContainer


def bootstrap() -> EgregoreContainer:
    """Production bootstrap. Call once at process start."""
    return EgregoreContainer.from_env()
