#!/usr/bin/env python3
"""egregore-admin -- Recovery CLI for FreezeController.

SEL-X is fail-closed. Once frozen, the system stays frozen until a human
operator explicitly verifies the incident and authorizes recovery.
This CLI is the controlled path from FROZEN -> RECONCILING -> HEALTHY.

Usage:
    # Check freeze status
    egregore-admin freeze-status --tenant-id test_tenant

    # Unfreeze (requires human acknowledgment)
    egregore-admin unfreeze --tenant-id test_tenant \
        --operator-id alice --reason "verified chain integrity"

    # Reset to HEALTHY (second step, only from RECONCILING)
    egregore-admin reset --tenant-id test_tenant \
        --operator-id alice --reason "SRE sign-off complete"

    # Show full freeze history
    egregore-admin freeze-history --tenant-id test_tenant
"""

from __future__ import annotations

import argparse
import secrets
import sys

from egregore.shared.canonical import canonical_dumps
from egregore.shared.freeze_state import FreezeController, FreezeEvent, FreezeState

# User management CLI imports (lazy to avoid importing persistence on freeze-only usage).


# In-memory registry (replace with persistent store in production).
_CONTROLLERS: dict[str, FreezeController] = {}


def get_controller(tenant_id: str) -> FreezeController:
    """Retrieve or create controller for tenant."""
    if tenant_id not in _CONTROLLERS:
        _CONTROLLERS[tenant_id] = FreezeController(tenant_id=tenant_id)
    return _CONTROLLERS[tenant_id]


def _event_to_dict(ev: FreezeEvent) -> dict:
    return {
        "triggered_at_ns": ev.timestamp_ns,
        "state": ev.state.name,
        "reason": ev.reason,
        "detection_source": ev.detection_source,
        "operator_id": ev.operator_id,
        "block_hash_trigger": ev.block_hash_trigger,
        "stored_hash": ev.stored_hash,
        "recomputed_hash": ev.recomputed_hash,
        "signature_valid": ev.signature_valid,
        "context": ev.context,
    }


def cmd_freeze_status(args: argparse.Namespace) -> int:
    fc = get_controller(args.tenant_id)
    status = {
        "tenant_id": args.tenant_id,
        "state": fc.state.name,
        "is_frozen": fc.is_frozen,
        "is_reconciling": fc.is_reconciling,
        "history_count": len(fc.history),
    }
    print(canonical_dumps(status, indent=2))
    return 0


def cmd_unfreeze(args: argparse.Namespace) -> int:
    fc = get_controller(args.tenant_id)

    if fc.state == FreezeState.HEALTHY:
        print("ERROR: Already HEALTHY. Nothing to unfreeze.", file=sys.stderr)
        return 1

    if fc.state == FreezeState.RECONCILING:
        print(
            "ERROR: Already RECONCILING. Use 'reset' to return to HEALTHY.",
            file=sys.stderr,
        )
        return 1

    event = fc.unfreeze(reason=args.reason, operator_id=args.operator_id)
    print(f"UNFROZEN at {event.timestamp_ns}")
    print(f"  Operator: {args.operator_id}")
    print(f"  Reason: {args.reason}")
    print(f"  New state: {event.state.name}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    fc = get_controller(args.tenant_id)

    if fc.state != FreezeState.RECONCILING:
        print(
            f"ERROR: Cannot reset from {fc.state.name}. Must be RECONCILING first.",
            file=sys.stderr,
        )
        return 1

    event = fc.reset(reason=args.reason, operator_id=args.operator_id)
    print(f"RESET to HEALTHY at {event.timestamp_ns}")
    print(f"  Operator: {args.operator_id}")
    print(f"  Reason: {args.reason}")
    return 0


def cmd_freeze_history(args: argparse.Namespace) -> int:
    fc = get_controller(args.tenant_id)
    history = [_event_to_dict(ev) for ev in fc.history]
    print(canonical_dumps({"tenant_id": args.tenant_id, "events": history}, indent=2))
    return 0


def _get_user_repo():
    from egregore.infrastructure.persistence.user_repository import (
        SQLiteUserRepository,
    )

    return SQLiteUserRepository()


def cmd_users_create(args: argparse.Namespace) -> int:
    repo = _get_user_repo()
    # Default new users into the first admin account, or a default account.
    admin = (
        repo._conn()
        .execute("SELECT account_id FROM users WHERE roles_json LIKE '%admin%' LIMIT 1")
        .fetchone()
    )
    account_id = (
        admin["account_id"]
        if admin
        else repo.create_account(name="default", owner_user_id="cli")
    )
    user = repo.create_user(
        account_id=account_id,
        username=args.username,
        email=args.email,
        roles=[args.role],
        status="active",
    )
    for vertical in args.verticals:
        repo.grant_vertical(user.id, vertical, "write")
    print(
        canonical_dumps(
            {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "roles": user.roles,
            },
            indent=2,
        )
    )
    return 0


def cmd_users_list(_args: argparse.Namespace) -> int:
    repo = _get_user_repo()
    rows = (
        repo._conn()
        .execute(
            "SELECT id, username, email, status, roles_json FROM users ORDER BY created_at DESC"
        )
        .fetchall()
    )
    users = []
    for r in rows:
        users.append(
            {
                "id": str(r["id"]),
                "username": str(r["username"]),
                "email": r["email"],
                "status": str(r["status"]),
                "roles": repo._deserialize_roles(r["roles_json"]),
            }
        )
    print(canonical_dumps({"users": users}, indent=2))
    return 0


def cmd_users_grant(args: argparse.Namespace) -> int:
    repo = _get_user_repo()
    repo.grant_vertical(args.user_id, args.cell_id, args.permission)
    print(
        canonical_dumps(
            {
                "ok": True,
                "user_id": args.user_id,
                "cell_id": args.cell_id,
                "permission": args.permission,
            },
            indent=2,
        )
    )
    return 0


def cmd_users_invite(args: argparse.Namespace) -> int:
    import datetime

    from egregore.models.user import Invite

    repo = _get_user_repo()
    code = f"INV-{secrets.token_urlsafe(16)}"
    verticals = (
        args.verticals if args.verticals else ([args.vertical] if args.vertical else [])
    )
    invite = Invite(
        code=code,
        issued_by=args.issued_by or "cli",
        issued_to=args.issued_to,
        role=args.role,
        vertical=verticals[0] if verticals else None,
        expires_at=int(datetime.datetime.now(datetime.UTC).timestamp() * 1e9)
        + args.expires_in_seconds * int(1e9),
        verticals=verticals,
    )
    repo.save_invite(invite)
    print(
        canonical_dumps(
            {"code": code, "role": args.role, "verticals": verticals}, indent=2
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="egregore-admin",
        description="SEL-X freeze recovery, audit, and user management CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_status = subparsers.add_parser("freeze-status", help="Show current freeze state")
    p_status.add_argument("--tenant-id", required=True, help="Tenant ID")
    p_status.set_defaults(func=cmd_freeze_status)

    p_unfreeze = subparsers.add_parser(
        "unfreeze", help="Move FROZEN -> RECONCILING (requires operator)"
    )
    p_unfreeze.add_argument("--tenant-id", required=True)
    p_unfreeze.add_argument("--operator-id", required=True, help="Who is authorizing")
    p_unfreeze.add_argument("--reason", required=True, help="Why it's safe to unfreeze")
    p_unfreeze.set_defaults(func=cmd_unfreeze)

    p_reset = subparsers.add_parser(
        "reset", help="Move RECONCILING -> HEALTHY (requires operator)"
    )
    p_reset.add_argument("--tenant-id", required=True)
    p_reset.add_argument("--operator-id", required=True)
    p_reset.add_argument("--reason", required=True)
    p_reset.set_defaults(func=cmd_reset)

    p_history = subparsers.add_parser(
        "freeze-history", help="Show full freeze event log"
    )
    p_history.add_argument("--tenant-id", required=True)
    p_history.set_defaults(func=cmd_freeze_history)

    # ------------------------------------------------------------------ #
    # Users / accounts
    # ------------------------------------------------------------------ #
    p_users = subparsers.add_parser("users", help="Manage users and vertical grants")
    user_sub = p_users.add_subparsers(dest="users_command", required=True)

    p_users_create = user_sub.add_parser("create", help="Create a user")
    p_users_create.add_argument("username", help="Unique username")
    p_users_create.add_argument("--email", default=None)
    p_users_create.add_argument(
        "--role", choices=["admin", "user", "guest"], required=True
    )
    p_users_create.add_argument(
        "--vertical", default=None, help="Legacy single vertical"
    )
    p_users_create.add_argument(
        "--verticals", nargs="*", default=[], help="Cell IDs the user may access"
    )
    p_users_create.set_defaults(func=cmd_users_create)

    p_users_list = user_sub.add_parser("list", help="List users")
    p_users_list.set_defaults(func=cmd_users_list)

    p_users_grant = user_sub.add_parser(
        "grant", help="Grant vertical permission to a user"
    )
    p_users_grant.add_argument("user_id", help="User ID")
    p_users_grant.add_argument("cell_id", help="Cell / vertical ID")
    p_users_grant.add_argument(
        "--permission", choices=["read", "write"], default="write"
    )
    p_users_grant.set_defaults(func=cmd_users_grant)

    p_users_invite = user_sub.add_parser("invite", help="Create an invite code")
    p_users_invite.add_argument(
        "--role", choices=["admin", "user", "guest"], required=True
    )
    p_users_invite.add_argument("--issued-by", default="cli")
    p_users_invite.add_argument("--issued-to", default=None)
    p_users_invite.add_argument("--vertical", default=None)
    p_users_invite.add_argument("--verticals", nargs="*", default=[])
    p_users_invite.add_argument("--expires-in-seconds", type=int, default=86400)
    p_users_invite.set_defaults(func=cmd_users_invite)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
