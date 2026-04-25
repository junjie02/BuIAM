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
                name TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                static_capabilities TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tokens (
                jti TEXT PRIMARY KEY,
                sub TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                actor_type TEXT NOT NULL DEFAULT 'agent',
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
        ensure_column(connection, "tokens", "actor_type", "actor_type TEXT NOT NULL DEFAULT 'agent'")
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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                caller_agent_id TEXT,
                claimed_agent_id TEXT,
                token_jti TEXT,
                token_sub TEXT,
                token_agent_id TEXT,
                delegated_user TEXT,
                token_fingerprint TEXT,
                token_issued_at INTEGER,
                token_expires_at INTEGER,
                verified_at INTEGER NOT NULL,
                is_expired INTEGER,
                is_revoked INTEGER,
                is_jti_registered INTEGER,
                signature_valid INTEGER,
                issuer_valid INTEGER,
                audience_valid INTEGER,
                identity_decision TEXT NOT NULL,
                error_code TEXT,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS intent_tree (
                node_id TEXT PRIMARY KEY,
                parent_node_id TEXT,
                root_node_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                target_agent_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                intent TEXT NOT NULL,
                description TEXT NOT NULL,
                data_refs TEXT NOT NULL,
                constraints TEXT NOT NULL,
                signature TEXT NOT NULL,
                signature_alg TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                judge_decision TEXT,
                judge_reason TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
