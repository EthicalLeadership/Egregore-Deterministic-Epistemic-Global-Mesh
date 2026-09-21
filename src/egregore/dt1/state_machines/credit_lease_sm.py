# epistemic marker: provenance / auditability
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from egregore.dt1.models import CreditGrant, CreditRevoke


class LeaseState(StrEnum):
    NO_CREDITS = "NO_CREDITS"
    ACTIVE = "ACTIVE"
    STALE = "STALE"


@dataclass(frozen=True)
class CreditLease:
    state: LeaseState

    credits_wu: int = 0
    credits_bytes: int = 0

    # TTL remaining in ms for deterministic “usable” checks.
    ttl_ms_remaining: int = 0
    epoch: int = 0


@dataclass(frozen=True)
class LeaseStepInputs:
    """
    Inputs to make the transition deterministic without wall-clock.

    Caller controls:
    - ttl_expired: whether the active lease has expired this tick
    - grant: optional grant applied this tick
    - revoke: optional revoke applied this tick
    """

    ttl_expired: bool

    grant: CreditGrant | None = None
    revoke: CreditRevoke | None = None


def step_credit_lease(lease: CreditLease, *, inputs: LeaseStepInputs) -> CreditLease:
    # Highest priority: revoke
    if inputs.revoke is not None:
        if inputs.revoke.epoch == lease.epoch and lease.state == LeaseState.ACTIVE:
            return CreditLease(state=LeaseState.NO_CREDITS, epoch=lease.epoch)
        # Epoch mismatch or non-active: preserve fail-safe (stay NO_CREDITS / no-op).
        return CreditLease(state=LeaseState.NO_CREDITS, epoch=lease.epoch)

    # Expiration moves ACTIVE -> STALE, STALE -> NO_CREDITS.
    if inputs.ttl_expired:
        if lease.state == LeaseState.ACTIVE:
            return CreditLease(
                state=LeaseState.STALE,
                credits_wu=lease.credits_wu,
                credits_bytes=lease.credits_bytes,
                ttl_ms_remaining=0,
                epoch=lease.epoch,
            )
        if lease.state == LeaseState.STALE:
            return CreditLease(state=LeaseState.NO_CREDITS, epoch=lease.epoch)
        return CreditLease(state=LeaseState.NO_CREDITS, epoch=lease.epoch)

    # Grants
    if inputs.grant is not None:
        # Epoch mismatch is treated as stale/revoked.
        if (
            lease.state in {LeaseState.ACTIVE, LeaseState.STALE}
            and inputs.grant.epoch != lease.epoch
        ):
            return CreditLease(state=LeaseState.NO_CREDITS, epoch=lease.epoch)

        # Valid grant transitions/refreshes ACTIVE.
        return CreditLease(
            state=LeaseState.ACTIVE,
            credits_wu=inputs.grant.credits_wu,
            credits_bytes=inputs.grant.credits_bytes,
            ttl_ms_remaining=inputs.grant.ttl_ms,
            epoch=inputs.grant.epoch,
        )

    # No events this tick: ACTIVE remains ACTIVE, NO_CREDITS remains NO_CREDITS.
    return lease
