"""Persistent user/account/invite/vertical-grant repository.

Default backend is SQLite, using the same node database as the dossier
persistence layer so no extra connection configuration is required.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Protocol

from egregore.models.user import ApiKey, Invite, User, UserIdentity, VerticalGrant
from egregore.shared.canonical import canonical_dumps, canonical_loads

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class IUserRepository(Protocol):
    def get_user_by_id(self, user_id: str) -> User | None: ...
    def get_user_by_email(self, email: str) -> User | None: ...
    def get_user_by_api_key_hash(self, key_hash: str) -> User | None: ...
    def create_user(
        self,
        *,
        account_id: str,
        username: str,
        email: str | None,
        roles: list[str],
        status: str = "active",
        user_id: str | None = None,
    ) -> User: ...
    def save_user(self, user: User) -> None: ...
    def create_account(
        self, name: str, owner_user_id: str, account_id: str | None = None
    ) -> str: ...
    def get_account(self, account_id: str) -> dict | None: ...
    def save_invite(self, invite: Invite) -> None: ...
    def get_invite(self, code: str) -> Invite | None: ...
    def use_invite(self, code: str) -> None: ...
    def create_api_key(
        self,
        user_id: str,
        key_hash: str,
        name: str = "default",
        expires_at: int | None = None,
    ) -> ApiKey: ...
    def get_api_key(self, key_hash: str) -> ApiKey | None: ...
    def list_api_keys_for_user(self, user_id: str) -> list[ApiKey]: ...
    def grant_vertical(self, user_id: str, cell_id: str, permission: str) -> None: ...
    def revoke_vertical(self, user_id: str, cell_id: str) -> None: ...
    def list_vertical_grants(self, user_id: str) -> list[VerticalGrant]: ...
    def bootstrap_admin_if_needed(
        self, tenant_id: str, api_keys: dict[str, tuple[str, str, str]]
    ) -> User | None: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def default_db_path() -> Path:
    """Return the default SQLite path used by the rest of the system."""
    node_id = os.environ.get("EGREGORE_NODE_ID", "pioneer1")
    data_dir = Path(os.environ.get("EGREGORE_DATA_DIR", f"~/egregore_data/{node_id}"))
    return data_dir.expanduser() / "node.db"


def _hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _now_ns() -> int:

    return time.time_ns()


# ---------------------------------------------------------------------------
# SQLite implementation
# ---------------------------------------------------------------------------


class SQLiteUserRepository:
    """SQLite-backed implementation of IUserRepository.

    Uses the same node.db as SQLiteTransactionalPersistence so migrations and
    backups stay together.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._ensure_migrated()

    def _conn(self) -> sqlite3.Connection:
        c: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(str(self._db_path), check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute("PRAGMA synchronous=NORMAL;")
            c.execute("PRAGMA foreign_keys=ON;")
            c.row_factory = sqlite3.Row
            self._local.conn = c
        return c

    def _ensure_migrated(self) -> None:
        """Apply any pending migrations from the shared migration list."""
        c = self._conn()
        c.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at INTEGER DEFAULT (unixepoch()))"
        )
        from egregore.infrastructure.persistence.migrate import SQLITE_MIGRATIONS

        applied = {r[0] for r in c.execute("SELECT version FROM schema_migrations")}
        for version, name, fn in SQLITE_MIGRATIONS:
            if version not in applied:
                fn(c)
                c.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (?, ?)",
                    (version, name),
                )
                c.commit()

    # ------------------------------------------------------------------ #
    # Users
    # ------------------------------------------------------------------ #

    @staticmethod
    def _deserialize_roles(raw: str) -> list[str]:
        try:
            return canonical_loads(raw)
        except Exception:  # noqa: BLE001
            return []

    def _row_to_user(self, row: sqlite3.Row) -> User:
        return User(
            id=str(row["id"]),
            username=str(row["username"]),
            email=row["email"],
            roles=self._deserialize_roles(row["roles_json"]),
            vertical=None,
            status=str(row["status"]),
            created_at=int(row["created_at"]),
            last_login=int(row["last_login"]) if row["last_login"] else None,
            provenance=row["provenance"],
        )

    def get_user_by_id(self, user_id: str) -> User | None:
        row = (
            self._conn()
            .execute("SELECT * FROM users WHERE id = ?", (user_id,))
            .fetchone()
        )
        return self._row_to_user(row) if row else None

    def get_user_by_email(self, email: str) -> User | None:
        row = (
            self._conn()
            .execute("SELECT * FROM users WHERE email = ?", (email,))
            .fetchone()
        )
        return self._row_to_user(row) if row else None

    def get_user_by_api_key_hash(self, key_hash: str) -> User | None:
        row = (
            self._conn()
            .execute(
                """
            SELECT u.* FROM users u
            JOIN api_keys k ON k.user_id = u.id
            WHERE k.key_hash = ? AND k.status = 'active'
            AND (k.expires_at IS NULL OR k.expires_at > ?)
            """,
                (key_hash, _now_ns()),
            )
            .fetchone()
        )
        return self._row_to_user(row) if row else None

    def create_user(
        self,
        *,
        account_id: str,
        username: str,
        email: str | None,
        roles: list[str],
        status: str = "active",
        user_id: str | None = None,
    ) -> User:
        user = User(
            id=user_id or f"user-{uuid.uuid4().hex[:12]}",
            username=username,
            email=email,
            roles=list(roles),
            vertical=None,
            status=status,
            created_at=_now_ns(),
        )
        self._conn().execute(
            """
            INSERT INTO users (id, account_id, username, email, status, roles_json, created_at, last_login, provenance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user.id,
                account_id,
                user.username,
                user.email,
                user.status,
                canonical_dumps(user.roles),
                user.created_at,
                user.last_login,
                user.provenance,
            ),
        )
        self._conn().commit()
        return user

    def save_user(self, user: User) -> None:
        self._conn().execute(
            """
            UPDATE users
            SET username = ?, email = ?, status = ?, roles_json = ?, last_login = ?, provenance = ?
            WHERE id = ?
            """,
            (
                user.username,
                user.email,
                user.status,
                canonical_dumps(user.roles),
                user.last_login,
                user.provenance,
                user.id,
            ),
        )
        self._conn().commit()

    # ------------------------------------------------------------------ #
    # Accounts
    # ------------------------------------------------------------------ #

    def create_account(
        self, name: str, owner_user_id: str, account_id: str | None = None
    ) -> str:
        aid = account_id or f"acct-{uuid.uuid4().hex[:12]}"
        self._conn().execute(
            "INSERT INTO accounts (id, name, owner_user_id, created_at) VALUES (?, ?, ?, ?)",
            (aid, name, owner_user_id, _now_ns()),
        )
        self._conn().commit()
        return aid

    def get_account(self, account_id: str) -> dict | None:
        row = (
            self._conn()
            .execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
            .fetchone()
        )
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "owner_user_id": str(row["owner_user_id"]),
            "created_at": int(row["created_at"]),
        }

    def get_account_by_owner(self, owner_user_id: str) -> dict | None:
        row = (
            self._conn()
            .execute(
                "SELECT * FROM accounts WHERE owner_user_id = ? LIMIT 1",
                (owner_user_id,),
            )
            .fetchone()
        )
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "owner_user_id": str(row["owner_user_id"]),
            "created_at": int(row["created_at"]),
        }

    def get_account_id_for_user(self, user_id: str) -> str | None:
        row = (
            self._conn()
            .execute("SELECT account_id FROM users WHERE id = ? LIMIT 1", (user_id,))
            .fetchone()
        )
        return str(row["account_id"]) if row else None

    # ------------------------------------------------------------------ #
    # Invites
    # ------------------------------------------------------------------ #

    def _deserialize_verticals(self, raw: str) -> list[str]:
        try:
            return canonical_loads(raw)
        except Exception:  # noqa: BLE001
            return []

    def _row_to_invite(self, row: sqlite3.Row) -> Invite:
        verticals = self._deserialize_verticals(row["verticals_json"])
        return Invite(
            code=str(row["code"]),
            issued_by=str(row["issued_by"]),
            issued_to=row["issued_to_email"],
            role=str(row["role"]),
            vertical=verticals[0] if verticals else None,
            expires_at=int(row["expires_at"]),
            used_at=int(row["used_at"]) if row["used_at"] else None,
            status=str(row["status"]),
            provenance=row["provenance"],
        )

    def save_invite(self, invite: Invite) -> None:
        verticals = invite.verticals
        self._conn().execute(
            """
            INSERT OR REPLACE INTO invites
            (code, issued_by, issued_to_email, role, verticals_json, expires_at, used_at, status, provenance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invite.code,
                invite.issued_by,
                invite.issued_to,
                invite.role,
                canonical_dumps(verticals),
                invite.expires_at,
                invite.used_at,
                invite.status,
                invite.provenance,
            ),
        )
        self._conn().commit()

    def get_invite(self, code: str) -> Invite | None:
        row = (
            self._conn()
            .execute("SELECT * FROM invites WHERE code = ?", (code,))
            .fetchone()
        )
        return self._row_to_invite(row) if row else None

    def use_invite(self, code: str) -> None:
        self._conn().execute(
            "UPDATE invites SET status = 'used', used_at = ? WHERE code = ?",
            (_now_ns(), code),
        )
        self._conn().commit()

    # ------------------------------------------------------------------ #
    # API keys
    # ------------------------------------------------------------------ #

    def _row_to_api_key(self, row: sqlite3.Row) -> ApiKey:
        return ApiKey(
            key_hash=str(row["key_hash"]),
            user_id=str(row["user_id"]),
            name=str(row["name"]),
            created_at=int(row["created_at"]),
            last_used_at=int(row["last_used_at"]) if row["last_used_at"] else None,
            expires_at=int(row["expires_at"]) if row["expires_at"] else None,
            status=str(row["status"]),
        )

    def create_api_key(
        self,
        user_id: str,
        key_hash: str,
        name: str = "default",
        expires_at: int | None = None,
    ) -> ApiKey:
        key = ApiKey(
            key_hash=key_hash,
            user_id=user_id,
            name=name,
            created_at=_now_ns(),
            expires_at=expires_at,
        )
        self._conn().execute(
            """
            INSERT INTO api_keys (key_hash, user_id, name, created_at, last_used_at, expires_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key.key_hash,
                key.user_id,
                key.name,
                key.created_at,
                key.last_used_at,
                key.expires_at,
                key.status,
            ),
        )
        self._conn().commit()
        return key

    def get_api_key(self, key_hash: str) -> ApiKey | None:
        row = (
            self._conn()
            .execute("SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,))
            .fetchone()
        )
        return self._row_to_api_key(row) if row else None

    def list_api_keys_for_user(self, user_id: str) -> list[ApiKey]:
        rows = (
            self._conn()
            .execute(
                "SELECT * FROM api_keys WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            )
            .fetchall()
        )
        return [self._row_to_api_key(r) for r in rows]

    def touch_api_key(self, key_hash: str) -> None:
        self._conn().execute(
            "UPDATE api_keys SET last_used_at = ? WHERE key_hash = ?",
            (_now_ns(), key_hash),
        )
        self._conn().commit()

    # ------------------------------------------------------------------ #
    # Vertical grants
    # ------------------------------------------------------------------ #

    def grant_vertical(self, user_id: str, cell_id: str, permission: str) -> None:
        self._conn().execute(
            """
            INSERT INTO vertical_grants (user_id, cell_id, permission, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, cell_id) DO UPDATE SET permission = excluded.permission
            """,
            (user_id, cell_id, permission, _now_ns()),
        )
        self._conn().commit()

    def revoke_vertical(self, user_id: str, cell_id: str) -> None:
        self._conn().execute(
            "DELETE FROM vertical_grants WHERE user_id = ? AND cell_id = ?",
            (user_id, cell_id),
        )
        self._conn().commit()

    def list_vertical_grants(self, user_id: str) -> list[VerticalGrant]:
        rows = (
            self._conn()
            .execute("SELECT * FROM vertical_grants WHERE user_id = ?", (user_id,))
            .fetchall()
        )
        return [
            VerticalGrant(
                user_id=str(r["user_id"]),
                cell_id=str(r["cell_id"]),
                permission=str(r["permission"]),
            )
            for r in rows
        ]

    def list_users(self) -> list[dict[str, Any]]:
        """Return all users with their vertical grants."""
        rows = (
            self._conn()
            .execute(
                "SELECT id, username, email, status, roles_json, created_at FROM users ORDER BY created_at DESC"
            )
            .fetchall()
        )
        result = []
        for r in rows:
            grants = self.list_vertical_grants(str(r["id"]))
            result.append(
                {
                    "id": str(r["id"]),
                    "username": str(r["username"]),
                    "email": r["email"],
                    "status": str(r["status"]),
                    "roles": self._deserialize_roles(r["roles_json"]),
                    "vertical_grants": [
                        {"cell_id": g.cell_id, "permission": g.permission}
                        for g in grants
                    ],
                }
            )
        return result

    def get_user_with_grants(self, user_id: str) -> dict[str, Any] | None:
        """Return a single user with vertical grants, or None."""
        user = self.get_user_by_id(user_id)
        if user is None:
            return None
        grants = self.list_vertical_grants(user_id)
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "status": user.status,
            "roles": user.roles,
            "vertical_grants": [
                {"cell_id": g.cell_id, "permission": g.permission} for g in grants
            ],
        }

    # ------------------------------------------------------------------ #
    # Identity
    # ------------------------------------------------------------------ #

    def resolve_identity(
        self, key_hash: str, tenant_id: str = "default"
    ) -> UserIdentity | None:
        user = self.get_user_by_api_key_hash(key_hash)
        if user is None:
            return None
        account_row = (
            self._conn()
            .execute("SELECT account_id FROM users WHERE id = ? LIMIT 1", (user.id,))
            .fetchone()
        )
        return UserIdentity(
            tenant_id=tenant_id,
            user_id=user.id,
            username=user.username,
            email=user.email,
            roles=list(user.roles),
            vertical_grants=self.list_vertical_grants(user.id),
            status=user.status,
            account_id=str(account_row["account_id"]) if account_row else None,
        )

    # ------------------------------------------------------------------ #
    # Bootstrap
    # ------------------------------------------------------------------ #

    def bootstrap_admin_if_needed(
        self, tenant_id: str, api_keys: dict[str, tuple[str, str, str]]
    ) -> User | None:
        """Create an owner account/admin user from env-configured API keys if DB is empty."""
        c = self._conn()
        existing = c.execute("SELECT id FROM users LIMIT 1").fetchone()
        if existing:
            return None

        # Pick the first configured key as the owner bootstrap.
        if not api_keys:
            return None

        key, identity = next(iter(api_keys.items()))
        _tenant, user_name, role = identity
        key_hash = _hash_api_key(key)

        user = self.create_user(
            account_id="",  # will update after account creation
            username=user_name or "owner",
            email=None,
            roles=[role] if role == "admin" else ["admin", role],
            status="active",
            user_id=f"user-{user_name or 'owner'}",
        )
        account_id = self.create_account(
            name=f"{tenant_id}-owner",
            owner_user_id=user.id,
            account_id=f"acct-{tenant_id}",
        )
        self._conn().execute(
            "UPDATE users SET account_id = ? WHERE id = ?", (account_id, user.id)
        )
        self._conn().commit()
        self.create_api_key(user.id, key_hash, name="bootstrap")
        # Re-fetch with corrected account_id.
        return self.get_user_by_id(user.id)


# ---------------------------------------------------------------------------
# Module-level default (lazy)
# ---------------------------------------------------------------------------

_default_repo: SQLiteUserRepository | None = None


def get_default_user_repository() -> SQLiteUserRepository:
    global _default_repo
    if _default_repo is None:
        _default_repo = SQLiteUserRepository()
    return _default_repo
