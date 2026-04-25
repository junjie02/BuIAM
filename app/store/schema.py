from __future__ import annotations

import sqlite3
from pathlib import Path


DB_PATH = Path("data/audit.db")


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)


def ensure_column(connection: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_schema(db_path: Path = DB_PATH) -> None:
    with connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                agent_type TEXT NOT NULL,
                description TEXT NOT NULL,
                owner_org TEXT NOT NULL,
                allowed_resource_domains TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                endpoint TEXT NOT NULL,
                static_capabilities TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tokens (
                jti TEXT PRIMARY KEY,
                sub TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                delegated_user TEXT NOT NULL,
                capabilities TEXT NOT NULL,
                exp INTEGER NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jti_seen (
                jti TEXT PRIMARY KEY,
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                caller_agent_id TEXT NOT NULL,
                target_agent_id TEXT NOT NULL,
                requested_capabilities TEXT NOT NULL,
                effective_capabilities TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                delegation_chain TEXT NOT NULL DEFAULT '[]',
                decision_detail TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        ensure_column(
            connection,
            "audit_logs",
            "decision_detail",
            "decision_detail TEXT NOT NULL DEFAULT '{}'",
        )
        ensure_column(
            connection,
            "audit_logs",
            "delegation_chain",
            "delegation_chain TEXT NOT NULL DEFAULT '[]'",
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS delegation_chain (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                from_actor TEXT NOT NULL,
                to_agent_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                delegated_capabilities TEXT NOT NULL,
                missing_capabilities TEXT NOT NULL,
                decision TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
