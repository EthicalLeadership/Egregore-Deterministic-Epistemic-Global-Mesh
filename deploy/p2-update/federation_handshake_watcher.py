#!/usr/bin/env python3
"""
Federation handshake watcher.

Polls the configured Pioneer 2 endpoint and automatically initiates a
federation treaty + entropy exchange the moment the peer comes online.

Environment variables:
  PIONEER2_HOST          Peer hostname/IP (default: 192.168.2.10)
  PIONEER2_PORT          Peer API port   (default: 8443)
  EGREGORE_PORT          Local API port  (default: 8443)
  EGREGORE_API_KEY       Hex API key     (default: first key from .env)
  EGREGORE_NODE_ID       This node id    (default: pioneer1)
  PEER_NODE_ID           Peer node id    (default: pioneer2)
  POLL_INTERVAL          Seconds         (default: 5)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("federation-watcher")


def _load_api_key_from_dotenv(repo_root: Path) -> str | None:
    dotenv = repo_root / ".env"
    if not dotenv.exists():
        return None
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != "EGREGORE_API_KEYS":
            continue
        value = value.strip().strip("\"'")
        for entry in value.split(","):
            parts = entry.strip().split(":")
            if parts and len(parts[0]) == 64:
                return parts[0]
    return None


def _default_api_key() -> str:
    env_key = os.environ.get("EGREGORE_API_KEY", "").strip()
    if env_key:
        return env_key
    repo_root = Path(__file__).resolve().parents[1]
    key = _load_api_key_from_dotenv(repo_root)
    if key:
        return key
    logger.warning(
        "No EGREGORE_API_KEY found; peer requests will be rejected by APIKeyMiddleware."
    )
    return ""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    return int(raw) if raw.isdigit() else default


class FederationClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        node_id: str,
        peer_node_id: str,
        timeout: float = 10.0,
        verify: bool = False,
    ) -> None:
        import httpx

        self._client = httpx.Client(timeout=timeout, verify=verify)
        self._base = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key} if api_key else {}
        self._node_id = node_id
        self._peer_node_id = peer_node_id

    def health(self) -> bool:
        try:
            resp = self._client.get(
                f"{self._base}/health/ready", headers=self._headers
            )
            return resp.status_code == 200
        except Exception:
            return False

    def propose_treaty(
        self, treaty_id: str, clauses: list[str]
    ) -> dict[str, object] | None:
        import httpx

        payload = {
            "treaty_id": treaty_id,
            "parties": [self._node_id, self._peer_node_id],
            "clauses": clauses,
        }
        try:
            resp = self._client.post(
                f"{self._base}/api/v1/federation/treaty/propose",
                json=payload,
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("propose_treaty failed: %s %s", exc.response.status_code, exc.response.text)
            return None
        except Exception as exc:
            logger.error("propose_treaty failed: %s", exc)
            return None

    def ratify_treaty(self, treaty_id: str, signature: str) -> dict[str, object] | None:
        import httpx

        payload = {
            "treaty_id": treaty_id,
            "node_id": self._node_id,
            "signature": signature,
        }
        try:
            resp = self._client.post(
                f"{self._base}/api/v1/federation/treaty/ratify",
                json=payload,
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("ratify_treaty failed: %s %s", exc.response.status_code, exc.response.text)
            return None
        except Exception as exc:
            logger.error("ratify_treaty failed: %s", exc)
            return None

    def send_entropy(self, value: float) -> dict[str, object] | None:
        import httpx

        payload = {
            "source_node_id": self._node_id,
            "signal_type": "handshake",
            "value": value,
            "confidence": 1.0,
            "timestamp_ns": time.time_ns(),
            "signature": "",
        }
        try:
            resp = self._client.post(
                f"{self._base}/api/v1/federation/entropy",
                json=payload,
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("send_entropy failed: %s %s", exc.response.status_code, exc.response.text)
            return None
        except Exception as exc:
            logger.error("send_entropy failed: %s", exc)
            return None

    def close(self) -> None:
        self._client.close()


def _load_clauses() -> list[str]:
    from pathlib import Path

    from egregore.domain.federation_constitution import load_constitution

    cfg_path = Path(os.environ.get("EGREGORE_CONSTITUTION_PATH", "config/egregore_constitution.yaml"))
    if not cfg_path.is_absolute():
        repo_root = Path(__file__).resolve().parents[1]
        cfg_path = repo_root / cfg_path
    raw = cfg_path.read_text(encoding="utf-8")
    return list(load_constitution(raw).required_clauses())


def _handshake(
    local: FederationClient,
    peer: FederationClient,
    treaty_id: str,
    clauses: list[str],
) -> bool:
    signature = f"sig-{uuid.uuid4().hex}"

    # Propose locally first so this node knows about the treaty.
    logger.info("Proposing treaty locally: %s", treaty_id)
    local.propose_treaty(treaty_id, clauses)

    # Propose to the peer.
    logger.info("Proposing treaty to peer: %s", peer._base)
    peer.propose_treaty(treaty_id, clauses)

    # Ratify locally.
    logger.info("Ratifying treaty locally as %s", local._node_id)
    local.ratify_treaty(treaty_id, signature)

    # Ratify on the peer.
    logger.info("Ratifying treaty on peer as %s", peer._node_id)
    peer.ratify_treaty(treaty_id, signature)

    # Exchange entropy signals so both nodes participate in aggregation.
    logger.info("Exchanging entropy signals")
    peer.send_entropy(0.42)
    local.send_entropy(0.45)

    # Verify peer has an active treaty.  The peer may return either a single
    # treaty object or a list envelope {"active_treaties": [...], "count": n}.
    try:
        resp = peer._client.get(
            f"{peer._base}/api/v1/federation/treaty/active", headers=peer._headers
        )
        data = resp.json()
        active_treaty = _extract_active_treaty(data, treaty_id)
        if active_treaty:
            logger.info(
                "Handshake complete. Active treaty: %s",
                active_treaty.get("treaty_id"),
            )
            return True
        logger.warning("Peer did not report an active treaty after handshake: %s", data)
        return False
    except Exception as exc:
        logger.error("Failed to verify peer treaty: %s", exc)
        return False


def _extract_active_treaty(data: Any, expected_treaty_id: str) -> dict[str, Any] | None:
    """Return the active treaty from either a single-object or list-envelope response."""
    if not data:
        return None

    if isinstance(data, dict) and "active_treaties" in data:
        candidates = list(data.get("active_treaties") or [])
    elif isinstance(data, dict):
        candidates = [data]
    elif isinstance(data, list):
        candidates = list(data)
    else:
        return None

    for treaty in candidates:
        if not isinstance(treaty, dict):
            continue
        state = treaty.get("state") or treaty.get("status", "").upper()
        if state not in ("ACTIVE", "RATIFIED"):
            continue
        if treaty.get("treaty_id") == expected_treaty_id:
            return treaty
        # Accept the first active treaty if ID matching is not possible.
        if expected_treaty_id is None:
            return treaty

    return None


def main() -> int:  # noqa: C901
    parser = argparse.ArgumentParser(description="Federation handshake watcher")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Perform a single handshake attempt and exit (useful for testing).",
    )
    parser.add_argument(
        "--target",
        default=os.environ.get("PIONEER2_HOST", "192.168.2.10"),
        help="Pioneer 2 hostname or IP",
    )
    parser.add_argument(
        "--target-port",
        type=int,
        default=_env_int("PIONEER2_PORT", 8443),
        help="Pioneer 2 API port",
    )
    parser.add_argument(
        "--local-port",
        type=int,
        default=_env_int("EGREGORE_LOCAL_PORT", _env_int("EGREGORE_PORT", 8443)),
        help="Local Egregore API port (defaults to EGREGORE_PORT)",
    )
    parser.add_argument(
        "--scheme",
        default=os.environ.get("EGREGORE_SCHEME", "https"),
        help="URL scheme to use for the peer API",
    )
    parser.add_argument(
        "--local-scheme",
        default=os.environ.get("EGREGORE_LOCAL_SCHEME", os.environ.get("EGREGORE_SCHEME", "https")),
        help="URL scheme to use for the local API (defaults to EGREGORE_SCHEME)",
    )
    args = parser.parse_args()

    node_id = os.environ.get("EGREGORE_NODE_ID", "pioneer1")
    peer_node_id = os.environ.get("PEER_NODE_ID", "pioneer2")
    api_key = _default_api_key()
    peer_url = f"{args.scheme}://{args.target}:{args.target_port}"
    local_url = f"{args.local_scheme}://127.0.0.1:{args.local_port}"
    poll_interval = _env_int("POLL_INTERVAL", 5)

    clauses = _load_clauses()
    treaty_id = f"treaty-{node_id}-{peer_node_id}-{uuid.uuid4().hex[:8]}"

    logger.info(
        "Watcher starting: node=%s peer=%s peer_url=%s local_url=%s treaty_id=%s",
        node_id,
        peer_node_id,
        peer_url,
        local_url,
        treaty_id,
    )

    local = FederationClient(local_url, api_key, node_id, peer_node_id)
    peer = FederationClient(peer_url, api_key, node_id, peer_node_id)

    if args.once:
        logger.info("Single-shot handshake mode")
        if not local.health():
            logger.error("Local API is not reachable at %s", local_url)
            return 1
        if not peer.health():
            logger.error("Peer is not reachable at %s", peer_url)
            return 1
        ok = _handshake(local, peer, treaty_id, clauses)
        local.close()
        peer.close()
        return 0 if ok else 1

    connected = False
    handshake_done = False
    attempts = 0
    try:
        while True:
            attempts += 1
            peer_reachable = peer.health()
            local_reachable = local.health()

            if peer_reachable and not connected:
                connected = True
                logger.info("Pioneer 2 is online at %s", peer_url)
            elif not peer_reachable and connected:
                connected = False
                handshake_done = False
                logger.warning("Pioneer 2 went offline (%s)", peer_url)

            if connected and not handshake_done:
                if not local_reachable:
                    logger.warning(
                        "Peer is online but local API (%s) is not reachable; skipping handshake",
                        local_url,
                    )
                else:
                    if _handshake(local, peer, treaty_id, clauses):
                        handshake_done = True
                    else:
                        logger.warning("Handshake attempt failed; will retry on next cycle")

            if attempts % 12 == 0 or (not connected and attempts == 1):
                logger.info(
                    "Polling status: peer_reachable=%s local_reachable=%s handshake_done=%s",
                    peer_reachable,
                    local_reachable,
                    handshake_done,
                )

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info("Watcher stopped by user")
    finally:
        local.close()
        peer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
