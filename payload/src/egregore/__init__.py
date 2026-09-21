"""
Egregore runtime skeleton (spec-integrity first).

This package is intentionally dependency-light for CI; adapters for optional
systems (dfih/ANCHORUM/NATS/NVML) are injected or stubbed for tests.
"""

from .version import __version__

__all__ = ["__version__"]
