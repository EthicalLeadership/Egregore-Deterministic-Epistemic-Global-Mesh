"""SEL-X integrity watcher.

Polls a block store, recomputes block integrity hashes, verifies chain linkage,
and triggers a freeze when tampering or chain breaks are detected.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from egregore.domain.execution_block import ExecutionBlock
from egregore.shared.freeze_state import FreezeController

logger = logging.getLogger(__name__)


class BlockStorePort(Protocol):
    """Port interface -- concrete PostgresBlockStore implements this."""

    def list_blocks(
        self, tenant_id: str, limit: int = 1000
    ) -> Sequence[ExecutionBlock]: ...

    def get_latest(self, tenant_id: str) -> ExecutionBlock | None: ...

    def get_by_height(self, tenant_id: str, height: int) -> ExecutionBlock | None: ...


VerifierFunc = Callable[[bytes, str, str], bool]
"""Signature: verifier(payload_bytes, signature_hex, public_key_hex) -> bool"""


@dataclass
class IntegrityReport:
    """Result of a single watch cycle."""

    checked_at: datetime
    blocks_checked: int
    blocks_passed: int
    blocks_failed: int
    chain_valid: bool
    freeze_triggered: bool
    first_failure_reason: str | None = None
    first_failure_block_hash: str | None = None
    first_failure_height: int | None = None
    chain_break_at_height: int | None = None
    expected_previous_hash: str | None = None
    actual_previous_hash: str | None = None


class IntegrityWatcher:
    """Async tamper-detection loop for a single tenant -- now with chain verification.

    Detection triggers:
    - Recomputed integrity_hash differs from stored block_hash
    - Previous-block hash chain is broken
    - Block height is not strictly monotonic (gaps or duplicates)
    - Block signature fails verification (if public_key provided)
    - Block store read fails

    The polling loop is fail-closed: it exits after the first freeze event.
    """

    def __init__(
        self,
        block_store: BlockStorePort,
        freeze_controller: FreezeController,
        tenant_id: str,
        public_key: str | None = None,
        verifier: VerifierFunc | None = None,
        interval_sec: float = 5.0,
        fail_closed: bool = True,
    ) -> None:
        self._block_store = block_store
        self._freeze_controller = freeze_controller
        self._tenant_id = tenant_id
        self._public_key = public_key
        self._verifier = verifier or self._default_verifier
        self._interval_sec = interval_sec
        self._fail_closed = fail_closed
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._last_report: IntegrityReport | None = None

    @property
    def last_report(self) -> IntegrityReport | None:
        return self._last_report

    async def start(self) -> None:
        """Start the background polling task."""
        if self._task is not None and not self._task.done():
            raise RuntimeError("Watcher already running")
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("[IntegrityWatcher] Started for tenant=%s", self._tenant_id)

    async def stop(self) -> None:
        """Stop the background polling task."""
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("[IntegrityWatcher] Stopped for tenant=%s", self._tenant_id)

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            report = self.check_once()
            self._last_report = report

            if report.freeze_triggered and self._fail_closed:
                logger.critical(
                    "[IntegrityWatcher] FREEZE triggered for tenant=%s. "
                    "Reason: %s. Block: %s, Height: %s",
                    self._tenant_id,
                    report.first_failure_reason,
                    report.first_failure_block_hash,
                    report.first_failure_height,
                )
                break

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._interval_sec
                )

    def check_once(self) -> IntegrityReport:
        """Run a single integrity check cycle.

        Returns an IntegrityReport; if a freeze was triggered, the report
        contains forensic details about the first failure.
        """
        checked_at = datetime.now(UTC)
        blocks_passed = 0
        blocks_failed = 0
        freeze_triggered = False
        first_failure_reason: str | None = None
        first_failure_block_hash: str | None = None
        first_failure_height: int | None = None
        chain_break_at_height: int | None = None
        expected_previous_hash: str | None = None
        actual_previous_hash: str | None = None

        try:
            blocks = list(self._block_store.list_blocks(self._tenant_id, limit=1000))
        except Exception as exc:
            logger.exception("Block store read failed -- treating as tamper event")
            self._freeze_controller.freeze(
                reason=f"Block store read failed: {exc}",
                detection_source="integrity_watcher",
            )
            return IntegrityReport(
                checked_at=checked_at,
                blocks_checked=0,
                blocks_passed=0,
                blocks_failed=0,
                chain_valid=False,
                freeze_triggered=True,
                first_failure_reason=f"Block store read failed: {exc}",
            )

        if not blocks:
            return IntegrityReport(
                checked_at=checked_at,
                blocks_checked=0,
                blocks_passed=0,
                blocks_failed=0,
                chain_valid=True,
                freeze_triggered=False,
            )

        blocks_sorted = sorted(blocks, key=lambda b: b.block_height)

        # Phase 1: Individual block integrity (signature + hash)
        for block in blocks_sorted:
            stored_hash = block.integrity_hash or block.block_hash or ""

            if self._public_key and block.block_signature:
                sig_valid = self._verifier(
                    block.canonical_payload,
                    block.block_signature,
                    self._public_key,
                )
                if not sig_valid:
                    blocks_failed += 1
                    freeze_triggered = True
                    first_failure_reason = first_failure_reason or "SIGNATURE_INVALID"
                    first_failure_block_hash = first_failure_block_hash or stored_hash
                    first_failure_height = first_failure_height or block.block_height
                    self._freeze_controller.freeze(
                        reason="SIGNATURE_INVALID: signature verification failed",
                        detection_source="integrity_watcher",
                        block_hash_trigger=stored_hash,
                        signature_valid=False,
                    )
                    break

            recomputed = hashlib.sha256(block.canonical_payload).hexdigest()
            if recomputed != stored_hash:
                blocks_failed += 1
                freeze_triggered = True
                first_failure_reason = first_failure_reason or "HASH_MISMATCH"
                first_failure_block_hash = first_failure_block_hash or stored_hash
                first_failure_height = first_failure_height or block.block_height
                self._freeze_controller.freeze(
                    reason="HASH_MISMATCH: block hash mismatch -- possible tampering",
                    detection_source="integrity_watcher",
                    block_hash_trigger=stored_hash,
                    stored_hash=stored_hash,
                    recomputed_hash=recomputed,
                    signature_valid=True,
                )
                break

            blocks_passed += 1

        if freeze_triggered:
            return IntegrityReport(
                checked_at=checked_at,
                blocks_checked=len(blocks_sorted),
                blocks_passed=blocks_passed,
                blocks_failed=blocks_failed,
                chain_valid=False,
                freeze_triggered=True,
                first_failure_reason=first_failure_reason,
                first_failure_block_hash=first_failure_block_hash,
                first_failure_height=first_failure_height,
            )

        # Phase 2: Chain linkage verification
        chain_valid, chain_error = self._verify_chain(blocks_sorted)

        if not chain_valid:
            freeze_triggered = True
            first_failure_reason = chain_error.reason
            first_failure_block_hash = chain_error.block_hash
            first_failure_height = chain_error.height
            chain_break_at_height = chain_error.height
            expected_previous_hash = chain_error.expected
            actual_previous_hash = chain_error.actual

            self._freeze_controller.freeze(
                reason=chain_error.reason,
                detection_source="integrity_watcher",
                block_hash_trigger=chain_error.block_hash,
                stored_hash=chain_error.actual,
                recomputed_hash=chain_error.expected,
            )

            return IntegrityReport(
                checked_at=checked_at,
                blocks_checked=len(blocks_sorted),
                blocks_passed=blocks_passed,
                blocks_failed=blocks_failed + 1,
                chain_valid=False,
                freeze_triggered=True,
                first_failure_reason=first_failure_reason,
                first_failure_block_hash=first_failure_block_hash,
                first_failure_height=first_failure_height,
                chain_break_at_height=chain_break_at_height,
                expected_previous_hash=expected_previous_hash,
                actual_previous_hash=actual_previous_hash,
            )

        return IntegrityReport(
            checked_at=checked_at,
            blocks_checked=len(blocks_sorted),
            blocks_passed=blocks_passed,
            blocks_failed=0,
            chain_valid=True,
            freeze_triggered=False,
        )

    def _verify_chain(self, blocks: list[ExecutionBlock]) -> tuple[bool, ChainError]:
        """Verify chain linkage, monotonicity, and gap-freedom.

        Returns (True, empty ChainError) if chain is valid.
        Returns (False, ChainError) on first violation.
        """
        if len(blocks) <= 1:
            return True, ChainError("", 0, "", "", "")

        for i in range(1, len(blocks)):
            prev = blocks[i - 1]
            curr = blocks[i]

            expected_height = prev.block_height + 1
            if curr.block_height != expected_height:
                return False, ChainError(
                    reason=(
                        f"HEIGHT_GAP: expected height {expected_height}, "
                        f"got {curr.block_height}"
                    ),
                    block_hash=curr.integrity_hash or curr.block_hash or "",
                    height=curr.block_height,
                    expected=str(expected_height),
                    actual=str(curr.block_height),
                )

            if curr.previous_block_hash != (prev.integrity_hash or prev.block_hash):
                return False, ChainError(
                    reason=(
                        f"CHAIN_BREAK: previous_hash mismatch at height {curr.block_height}"
                    ),
                    block_hash=curr.integrity_hash or curr.block_hash or "",
                    height=curr.block_height,
                    expected=prev.integrity_hash or prev.block_hash or "",
                    actual=curr.previous_block_hash,
                )

        return True, ChainError("", 0, "", "", "")

    @staticmethod
    def _default_verifier(
        payload: bytes, signature_hex: str, public_key_hex: str
    ) -> bool:
        """Default Ed25519 verifier using PyNaCl."""
        try:
            from nacl.signing import VerifyKey

            vk = VerifyKey(bytes.fromhex(public_key_hex))
            vk.verify(payload, bytes.fromhex(signature_hex))
            return True
        except Exception:
            return False


@dataclass
class ChainError:
    """Structured error from chain verification."""

    reason: str
    height: int
    block_hash: str
    expected: str
    actual: str
