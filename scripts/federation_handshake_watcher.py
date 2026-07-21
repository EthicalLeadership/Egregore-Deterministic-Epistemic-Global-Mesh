#!/usr/bin/env python3
"""
Federation handshake watcher.

Polls the configured Pioneer 2 endpoint and automatically initiates a
federation treaty + entropy exchange the moment the peer comes online.

Environment variables:
  PIONEER2_HOST          Peer hostname/IP (default: 192.168.2.10)
  PIONEER2_PORT          Peer API port   (default: 8000)
  BLACKSTAR_PORT         Local API port  (default: 18000)
  BLACKSTAR_API_KEY      Hex API key     (default: first key from .env)
  BLACKSTAR_NODE_ID      This node id    (default: pioneer1)
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
        if key.strip() != "BLACKSTAR_API_KEYS":
            continue
        value = value.strip().strip("\"'")
        for entry in value.split(","):
            parts = entry.strip().split(":")
            if parts and len(parts[0]) == 64:
                return parts[0]
    return None


def _default_api_key() -> str:
    env_key = os.environ.get("BLACKSTAR_API_KEY", "").strip()
    if env_key:
        return env_key
    repo_root = Path(__file__).resolve().parents[1]
    key = _load_api_key_from_dotenv(repo_root)
    if key:
        return key
    logger.warning(
        "No BLACKSTAR_API_KEY found; peer requests will be rejected by APIKeyMiddleware."
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
    ) -> None:
        import httpx

        self._client = httpx.Client(timeout=timeout)
        self._base = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key} if api_key else {}
        self._node_id = node_id
        self._peer_node_id = peer_node_id

    def health(self) -> bool:
        try:
            resp = self._client.get(f"{self._base}/health", headers=self._headers)
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
    from egregore.domain.federation_constitution import load_constitution

    return list(load_constitution().required_clauses())


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

    # Verify peer has an active treaty.
    try:
        import httpx

        resp = peer._client.get(
            f"{peer._base}/api/v1/federation/treaty/active", headers=peer._headers
        )
        active = resp.json()
        if active and active.get("state") == "ACTIVE":
            logger.info("Handshake complete. Active treaty: %s", active.get("treaty_id"))
            return True
        logger.warning("Peer did not report an active treaty after handshake: %s", active)
        return False
    except Exception as exc:
        logger.error("Failed to verify peer treaty: %s", exc)
        return False


def main() -> int:
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
        default=_env_int("PIONEER2_PORT", 8000),
        help="Pioneer 2 API port",
    )
    parser.add_argument(
        "--local-port",
        type=int,
        default=_env_int("BLACKSTAR_PORT", 18000),
        help="Local Egregore API port",
    )
    args = parser.parse_args()

    node_id = os.environ.get("BLACKSTAR_NODE_ID", "pioneer1")
    peer_node_id = os.environ.get("PEER_NODE_ID", "pioneer2")
    api_key = _default_api_key()
    peer_url = f"http://{args.target}:{args.target_port}"
    local_url = f"http://127.0.0.1:{args.local_port}"
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
