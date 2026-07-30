#!/usr/bin/env python3
"""Dashboard server launcher with automatic port fallback.

Runs the Interface Synod dashboard via uvicorn. If the requested port is in
use, the launcher scans upward for the next free port and prints the actual
URL before starting.

Environment variables:
  DASHBOARD_HOST  Bind host (default: 127.0.0.1).
  DASHBOARD_PORT  Preferred port (default: 8000).
  EGREGORE_SANDBOX_OUTPUT  Directory containing aggregate_report.json.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys


def _find_free_port(host: str, preferred: int, max_attempts: int = 100) -> int:
    """Return ``preferred`` if free, otherwise the next available port."""
    for port in range(preferred, preferred + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(
        f"Could not find a free port in {preferred}-{preferred + max_attempts - 1}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Launch the Egregore Interface Synod dashboard"
    )
    parser.add_argument("--host", default=os.environ.get("DASHBOARD_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("DASHBOARD_PORT", "8000")),
    )
    parser.add_argument(
        "--sandbox-output",
        default=os.environ.get("EGREGORE_SANDBOX_OUTPUT", "sandbox_outputs"),
    )
    args = parser.parse_args(argv)

    os.environ.setdefault("EGREGORE_SANDBOX_OUTPUT", args.sandbox_output)

    port = _find_free_port(args.host, args.port)
    if port != args.port:
        print(f"Port {args.port} in use; using port {port} instead.", file=sys.stderr)
    print(f"Egregore dashboard running at http://{args.host}:{port}")

    # Import uvicorn here so missing dependency surfaces with a clean error.
    import uvicorn

    uvicorn.run(
        "egregore.tooling.dashboard.server:app",
        host=args.host,
        port=port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
