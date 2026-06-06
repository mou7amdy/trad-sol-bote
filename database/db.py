from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Union

from loguru import logger

from config.settings import settings


class DatabaseClient:
    """Unified async database client supporting SQLite (aiosqlite) and PostgreSQL (asyncpg).

    Auto-detects backend from ``settings.DATABASE_URL``.
    Falls back to aiosqlite with ``settings.SQLITE_DB_PATH`` if no URL is set.
    """

    def __init__(self) -> None:
        self._conn: Any = None
        self._pg = False
        self._connected = False

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def is_postgres(self) -> bool:
        return self._pg

    @property
    def connection(self) -> Any:
        return self._conn

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        url = settings.DATABASE_URL.strip()
        if url and url.startswith("postgresql"):
            import asyncpg
            self._pg = True
            self._conn = await asyncpg.connect(url)
            logger.info(f"Connected to PostgreSQL: {url.split('@')[-1]}")
        else:
            import aiosqlite
            self._pg = False
            path = settings.SQLITE_DB_PATH
            self._conn = await aiosqlite.connect(path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA foreign_keys = ON;")
            await self._conn.execute("PRAGMA journal_mode = WAL;")
            logger.info(f"Connected to SQLite: {path}")
        self._connected = True

    async def close(self) -> None:
        if not self._conn:
            return
        try:
            await self._conn.close()
        except Exception:
            pass
        self._connected = False

    async def ensure_connected(self) -> None:
        if self._connected:
            try:
                await self.execute("SELECT 1")
                return
            except Exception:
                logger.warning("DB connection lost, reconnecting...")
                await self.close()
        await self.connect()

    # ------------------------------------------------------------------
    # Placeholder conversion  (? -> $1, $2, …)
    # ------------------------------------------------------------------

    _PLACEHOLDER_RE = re.compile(r"\?")

    def _pg_sql(self, sql: str, params: Sequence[Any]) -> tuple[str, list[Any]]:
        parts = self._PLACEHOLDER_RE.split(sql)
        if len(parts) == 1:
            return sql, list(params)
        result = parts[0]
        args: list[Any] = []
        idx = 1
        for part in parts[1:]:
            result += f"${idx}{part}"
            idx += 1
        return result, list(params)

    # ------------------------------------------------------------------
    # DDL helpers
    # ------------------------------------------------------------------

    def auto_pk(self) -> str:
        return "SERIAL PRIMARY KEY" if self._pg else "INTEGER PRIMARY KEY AUTOINCREMENT"

    def insert_or_replace(self, table: str, cols: list[str], pk_col: str = "address") -> str:
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)

        if self._pg:
            updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != pk_col)
            return (
                f"INSERT INTO {table} ({col_list}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT ({pk_col}) DO UPDATE SET {updates}"
            )
        return f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"

    def insert_or_ignore(self, table: str, cols: list[str], conflict_col: str) -> str:
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)

        if self._pg:
            return (
                f"INSERT INTO {table} ({col_list}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict_col}) DO NOTHING"
            )
        return f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})"

    def now_expr(self) -> str:
        return "NOW()" if self._pg else "CURRENT_TIMESTAMP"

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        sql: str,
        params: Optional[Sequence[Any]] = None,
    ) -> Any:
        await self.ensure_connected()
        params = params or ()

        if self._pg:
            sql, args = self._pg_sql(sql, params)
            return await self._conn.execute(sql, *args)
        else:
            return await self._conn.execute(sql, params)

    async def execute_many(
        self,
        sql: str,
        params_list: Sequence[Sequence[Any]],
    ) -> None:
        await self.ensure_connected()

        if self._pg:
            sql, _ = self._pg_sql(sql, params_list[0] if params_list else [])
            await self._conn.executemany(sql, [list(p) for p in params_list])
        else:
            await self._conn.executemany(sql, params_list)

    async def fetchone(
        self,
        sql: str,
        params: Optional[Sequence[Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        await self.ensure_connected()
        params = params or ()

        if self._pg:
            sql, args = self._pg_sql(sql, params)
            row = await self._conn.fetchrow(sql, *args)
            return dict(row) if row else None
        else:
            async with self._conn.execute(sql, params) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def fetchall(
        self,
        sql: str,
        params: Optional[Sequence[Any]] = None,
    ) -> List[Dict[str, Any]]:
        await self.ensure_connected()
        params = params or ()

        if self._pg:
            sql, args = self._pg_sql(sql, params)
            rows = await self._conn.fetch(sql, *args)
            return [dict(r) for r in rows]
        else:
            async with self._conn.execute(sql, params) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def fetchval(
        self,
        sql: str,
        params: Optional[Sequence[Any]] = None,
    ) -> Any:
        await self.ensure_connected()
        params = params or ()

        if self._pg:
            sql, args = self._pg_sql(sql, params)
            return await self._conn.fetchval(sql, *args)
        else:
            async with self._conn.execute(sql, params) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    async def commit(self) -> None:
        if self._pg:
            return
        if self._conn:
            await self._conn.commit()


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

db: DatabaseClient = DatabaseClient()
