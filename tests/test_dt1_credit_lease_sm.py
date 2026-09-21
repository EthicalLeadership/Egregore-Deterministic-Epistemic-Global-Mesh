from egregore.dt1.models import CreditGrant, CreditRevoke, Priority
from egregore.dt1.state_machines.credit_lease_sm import (
    CreditLease,
    LeaseState,
    LeaseStepInputs,
    step_credit_lease,
)


def test_credit_lease_ttl_expired_active_to_stale_to_no_credits() -> None:
    lease = CreditLease(
        state=LeaseState.ACTIVE,
        credits_wu=10,
        credits_bytes=100,
        ttl_ms_remaining=50,
        epoch=1,
    )

    # tick: ttl expired
    lease2 = step_credit_lease(lease, inputs=LeaseStepInputs(ttl_expired=True))
    assert lease2.state == LeaseState.STALE
    assert lease2.ttl_ms_remaining == 0
    assert lease2.epoch == 1
    assert lease2.credits_wu == 10

    # tick: ttl expired again while stale -> no credits
    lease3 = step_credit_lease(lease2, inputs=LeaseStepInputs(ttl_expired=True))
    assert lease3.state == LeaseState.NO_CREDITS
    assert lease3.epoch == 1


def test_credit_lease_revoke_from_active_epoch_matches() -> None:
    lease = CreditLease(
        state=LeaseState.ACTIVE,
        credits_wu=10,
        credits_bytes=100,
        ttl_ms_remaining=50,
        epoch=7,
    )

    revoke = CreditRevoke(
        stage_id="cqb",
        site="mtl01",
        dt1_type="A",
        priority=Priority.P1,
        epoch=7,
    )

    lease2 = step_credit_lease(
        lease, inputs=LeaseStepInputs(ttl_expired=False, revoke=revoke)
    )
    assert lease2.state == LeaseState.NO_CREDITS
    assert lease2.epoch == 7


def test_credit_lease_revoke_wrong_epoch_fails_closed_to_no_credits() -> None:
    lease = CreditLease(
        state=LeaseState.ACTIVE,
        credits_wu=10,
        credits_bytes=100,
        ttl_ms_remaining=50,
        epoch=7,
    )

    revoke = CreditRevoke(
        stage_id="cqb",
        site="mtl01",
        dt1_type="A",
        priority=Priority.P1,
        epoch=8,
    )

    lease2 = step_credit_lease(
        lease, inputs=LeaseStepInputs(ttl_expired=False, revoke=revoke)
    )
    assert lease2.state == LeaseState.NO_CREDITS
    assert lease2.epoch == 7


def test_credit_lease_grant_epoch_mismatch_moves_to_no_credits() -> None:
    lease = CreditLease(
        state=LeaseState.ACTIVE,
        credits_wu=10,
        credits_bytes=100,
        ttl_ms_remaining=50,
        epoch=3,
    )

    grant = CreditGrant(
        stage_id="cqb",
        site="mtl01",
        dt1_type="A",
        priority=Priority.P2,
        credits_wu=20,
        credits_bytes=200,
        ttl_ms=999,
        epoch=4,  # mismatch
    )

    lease2 = step_credit_lease(
        lease, inputs=LeaseStepInputs(ttl_expired=False, grant=grant)
    )
    assert lease2.state == LeaseState.NO_CREDITS
    assert lease2.epoch == 3


def test_credit_lease_grant_sets_active_and_refreshes_ttl() -> None:
    lease = CreditLease(
        state=LeaseState.NO_CREDITS,
        credits_wu=0,
        credits_bytes=0,
        ttl_ms_remaining=0,
        epoch=0,
    )

    grant = CreditGrant(
        stage_id="cqb",
        site="mtl01",
        dt1_type="A",
        priority=Priority.P0,
        credits_wu=100,
        credits_bytes=1000,
        ttl_ms=250,
        epoch=11,
    )

    lease2 = step_credit_lease(
        lease, inputs=LeaseStepInputs(ttl_expired=False, grant=grant)
    )
    assert lease2.state == LeaseState.ACTIVE
    assert lease2.credits_wu == 100
    assert lease2.credits_bytes == 1000
    assert lease2.ttl_ms_remaining == 250
    assert lease2.epoch == 11
