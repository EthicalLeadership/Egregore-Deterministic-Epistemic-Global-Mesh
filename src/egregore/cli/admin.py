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
from pathlib import Path
import json
import getpass
import hashlib
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


def _nodes_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    nodes_dir = repo_root / "config" / "nodes"
    nodes_dir.mkdir(parents=True, exist_ok=True)
    return nodes_dir


def cmd_node_register(args: argparse.Namespace) -> int:
    nodes_dir = _nodes_dir()
    node_file = nodes_dir / f"{args.node_id}.json"

    data = {
        "node_id": args.node_id,
        "capabilities": args.capabilities,
        "resource_profile": {
            "cpu_percent": args.cpu_percent,
            "memory_mb": args.memory_mb,
            "vram_mb": args.vram_mb,
            "disk_iops": args.disk_iops,
            "network_mbps": args.network_mbps,
        },
        "trust_score": 0.5,
        "status": "ACTIVE",
        "last_heartbeat_ns": 0,
        "public_key_fingerprint": None,
    }

    node_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(canonical_dumps({"ok": True, "node_id": args.node_id, "file": str(node_file)}, indent=2))
    return 0


def cmd_node_list(args: argparse.Namespace) -> int:
    nodes_dir = _nodes_dir()
    files = sorted(nodes_dir.glob("*.json"))
    nodes = []
    for f in files:
        try:
            nodes.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    print(canonical_dumps({"nodes": nodes}, indent=2))
    return 0


def cmd_account_create(args: argparse.Namespace) -> int:
    repo = _get_user_repo()

    # 1. Create account with provisional owner, then fix owner after user exists.
    account_id = repo.create_account(name=args.account_name, owner_user_id="cli")

    # 2. Create admin user under that account.
    user = repo.create_user(
        account_id=account_id,
        username=args.admin_username,
        email=args.email,
        roles=["admin"],
        status="active",
    )

    # 3. Set real owner_user_id.
    repo._conn().execute(
        "UPDATE accounts SET owner_user_id = ? WHERE id = ?",
        (user.id, account_id),
    )
    repo._conn().commit()

    # 4. Password entry, never via argv.
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("ERROR: Passwords do not match.", file=sys.stderr)
        return 1
    if len(password) < 8:
        print("ERROR: Password must be at least 8 characters.", file=sys.stderr)
        return 1

    salt = secrets.token_bytes(16)
    n, r, p = 16384, 8, 1
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p).hex()
    stored = f"scrypt${n}${r}${p}${salt.hex()}${digest}"

    repo.set_password(user.id, stored)

    print(
        canonical_dumps(
            {
                "account_id": account_id,
                "account_name": args.account_name,
                "admin_user_id": user.id,
                "admin_username": user.username,
                "status": "created",
            },
            indent=2,
        )
    )
    return 0


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

    p_account = subparsers.add_parser(
        "account", help="Create an account with an admin user and password"
    )
    account_sub = p_account.add_subparsers(dest="account_command", required=True)

    p_account_create = account_sub.add_parser(
        "create", help="Create account, admin user, and set password"
    )
    p_account_create.add_argument("account_name", help="Unique account name")
    p_account_create.add_argument("admin_username", help="Admin username")
    p_account_create.add_argument("--email", default=None)
    p_account_create.set_defaults(func=cmd_account_create)

    p_node = subparsers.add_parser("node", help="Register or list compute nodes")
    node_sub = p_node.add_subparsers(dest="node_command", required=True)

    p_node_register = node_sub.add_parser("register", help="Register a node")
    p_node_register.add_argument("node_id", help="Unique node identifier")
    p_node_register.add_argument(
        "--capabilities", nargs="*", default=[], help="Capability tags e.g. llm gpu"
    )
    p_node_register.add_argument("--cpu-percent", type=float, default=0.0)
    p_node_register.add_argument("--memory-mb", type=int, default=0)
    p_node_register.add_argument("--vram-mb", type=int, default=0)
    p_node_register.add_argument("--disk-iops", type=int, default=0)
    p_node_register.add_argument("--network-mbps", type=int, default=0)
    p_node_register.set_defaults(func=cmd_node_register)

    p_node_list = node_sub.add_parser("list", help="List registered nodes")
    p_node_list.set_defaults(func=cmd_node_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
