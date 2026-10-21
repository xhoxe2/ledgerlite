"""Connection pool and schema for the wallet database."""

from pathlib import Path

import asyncpg

SCHEMA_SQL = Path(__file__).with_name("schema.sql").read_text()


async def create_pool(database_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(database_url)
    # shortcut: the schema is applied on every start and is safe with one process only;
    # move to migrations (Alembic) once the schema starts to change.
    await pool.execute(SCHEMA_SQL)
    return pool
