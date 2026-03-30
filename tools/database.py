"""
Database access via SOCKS5 tunnel (frp).

Architecture note:
  Python Script → pysocks (127.0.0.1:1080) → frpc visitor → frps → frpc client → SQL Server

Important: pytds is a pure-Python TDS implementation. It MUST be used instead of pymssql
(C library) because C extensions bypass Python's socket layer and won't work through SOCKS5.

Socket patching is global to the process. We serialize all DB calls behind a threading.Lock
and restore the original socket after each connection is established, so the patched socket
only affects the initial TCP handshake — after that pytds holds its own socket reference.
"""
from __future__ import annotations

import asyncio
import socket
import threading
from typing import Any

import socks

from orchestration.state import DBConfig

_db_lock = threading.Lock()
_original_socket = socket.socket


def _connect_sync(config: DBConfig):
    """Establish a pytds connection through the SOCKS5 proxy (blocking).

    Must run inside _db_lock to prevent concurrent socket patching.
    """
    import pytds  # imported here so the patched socket is in place at import time

    if config.socks5_user:
        socks.set_default_proxy(
            socks.SOCKS5,
            config.socks5_host,
            config.socks5_port,
            username=config.socks5_user,
            password=config.socks5_pass,
        )
    else:
        socks.set_default_proxy(socks.SOCKS5, config.socks5_host, config.socks5_port)

    socket.socket = socks.socksocket  # type: ignore[assignment]
    try:
        conn = pytds.connect(
            server=config.db_host,
            port=config.db_port,
            user=config.db_user,
            password=config.db_pass,
            database=config.db_name,
            timeout=30,
        )
    finally:
        # Restore after the TCP handshake; the established conn holds its own fd
        socket.socket = _original_socket  # type: ignore[assignment]

    return conn


def _fetch_table_list_sync(config: DBConfig) -> list[str]:
    """Return a flat list of all base-table names (no column details)."""
    with _db_lock:
        conn = _connect_sync(config)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
        """)
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def _fetch_table_columns_sync(config: DBConfig, tables: list[str]) -> dict[str, list[dict]]:
    """Return column details for the specified tables only.

    Returns: {table_name: [{name, type, nullable, is_pk}]}
    """
    if not tables:
        return {}

    with _db_lock:
        conn = _connect_sync(config)
    try:
        cursor = conn.cursor()
        result: dict[str, list[dict]] = {}
        for table in tables:
            # Column info
            cursor.execute(
                """
                SELECT c.COLUMN_NAME, c.DATA_TYPE, c.IS_NULLABLE,
                       c.CHARACTER_MAXIMUM_LENGTH, c.NUMERIC_PRECISION
                FROM INFORMATION_SCHEMA.COLUMNS c
                WHERE c.TABLE_NAME = %s
                ORDER BY c.ORDINAL_POSITION
                """,
                (table,),
            )
            cols = cursor.fetchall()

            # Primary key info
            cursor.execute(
                """
                SELECT kcu.COLUMN_NAME
                FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                  ON kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
                WHERE tc.TABLE_NAME = %s AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
                """,
                (table,),
            )
            pk_cols = {row[0] for row in cursor.fetchall()}

            result[table] = [
                {
                    "name": c[0],
                    "type": c[1],
                    "nullable": c[2] == "YES",
                    "is_pk": c[0] in pk_cols,
                }
                for c in cols
            ]
        return result
    finally:
        conn.close()


async def get_table_list(config: DBConfig) -> list[str]:
    """Async: return all table names (lightweight, no column details)."""
    return await asyncio.to_thread(_fetch_table_list_sync, config)


async def get_table_columns(config: DBConfig, tables: list[str]) -> dict[str, list[dict]]:
    """Async: return full column details for the given tables only."""
    return await asyncio.to_thread(_fetch_table_columns_sync, config, tables)


def format_columns(columns: dict[str, list[dict]]) -> str:
    """Format column details into a compact, LLM-readable string."""
    lines: list[str] = []
    for table, cols in columns.items():
        col_strs = []
        for c in cols:
            tag = " [PK]" if c["is_pk"] else ""
            nullable = "?" if c["nullable"] else ""
            col_strs.append(f"{c['name']}{tag} ({c['type']}{nullable})")
        lines.append(f"[{table}]\n  " + "\n  ".join(col_strs))
    return "\n\n".join(lines)


def _execute_sync(config: DBConfig, sql: str, params: tuple[Any, ...] = ()) -> list[tuple]:
    """Execute a parameterized query and return all rows (blocking)."""
    with _db_lock:
        conn = _connect_sync(config)
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        conn.close()


async def execute_query(config: DBConfig, sql: str, params: tuple[Any, ...] = ()) -> list[tuple]:
    """Async wrapper for execute_sync."""
    return await asyncio.to_thread(_execute_sync, config, sql, params)
