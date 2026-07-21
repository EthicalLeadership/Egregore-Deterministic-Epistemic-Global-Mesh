"""
BLACKSTAR LAW: Schema Migration Harness
Idempotent, up-only, version-tracked in schema_migrations table.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

Migrations = list[tuple[int, str, Callable]]

SQLITE_MIGRATIONS: Migrations = [
    (
        1,
        "init_case_versions_and_dossier_commits",
        lambda conn: (
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at INTEGER DEFAULT (unixepoch())
                );

                CREATE TABLE IF NOT EXISTS case_versions (
                    organization_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    next_version INTEGER NOT NULL DEFAULT 1,
                    case_next_state TEXT NOT NULL DEFAULT 'active',
                    PRIMARY KEY (organization_id, case_id)
                );

                CREATE TABLE IF NOT EXISTS dossier_commits (
                    execution_id TEXT NOT NULL PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    version_number INTEGER NOT NULL,
                    version_id TEXT NOT NULL,
                    timestamp_ns INTEGER NOT NULL,
                    case_next_state TEXT NOT NULL,
                    command_json TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    events_json TEXT NOT NULL,
                    outbox_json TEXT NOT NULL,
                    usage_deltas_json TEXT NOT NULL,
                    zarc_emitted INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(organization_id, case_id, version_number)
                );
                """),
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (1, ?)",
                ("init_case_versions_and_dossier_commits",),
            ),
        ),
    ),
    (
        2,
        "init_accounts_users_invites_grants_api_keys",
        lambda conn: conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT NOT NULL PRIMARY KEY,
                name TEXT NOT NULL,
                owner_user_id TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id TEXT NOT NULL PRIMARY KEY,
                account_id TEXT NOT NULL,
                username TEXT NOT NULL,
                email TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                roles_json TEXT NOT NULL DEFAULT '[]',
                created_at INTEGER NOT NULL,
                last_login INTEGER,
                provenance TEXT,
                UNIQUE(username),
                UNIQUE(email)
            );

            CREATE TABLE IF NOT EXISTS vertical_grants (
                user_id TEXT NOT NULL,
                cell_id TEXT NOT NULL,
                permission TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, cell_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS invites (
                code TEXT NOT NULL PRIMARY KEY,
                issued_by TEXT NOT NULL,
                issued_to_email TEXT,
                role TEXT NOT NULL,
                verticals_json TEXT NOT NULL DEFAULT '[]',
                expires_at INTEGER NOT NULL,
                used_at INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                provenance TEXT
            );

            CREATE TABLE IF NOT EXISTS api_keys (
                key_hash TEXT NOT NULL PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT 'default',
                created_at INTEGER NOT NULL,
                last_used_at INTEGER,
                expires_at INTEGER,
                status TEXT NOT NULL DEFAULT 'active',
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_users_account_id ON users(account_id);
            CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);
            CREATE INDEX IF NOT EXISTS idx_vertical_grants_user_id ON vertical_grants(user_id);
            """),
    ),
]


def migrate_sqlite(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at INTEGER DEFAULT (unixepoch()))"
        )
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        for version, name, fn in SQLITE_MIGRATIONS:
            if version not in applied:
                fn(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                    (version, name),
                )
                conn.commit()
                print(f"Applied migration {version}: {name}")
        conn.commit()
    finally:
        conn.close()
