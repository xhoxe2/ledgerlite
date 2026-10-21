"""GET /health: the wallet answers and can query its database."""

from typing import Literal

import asyncpg
from fastapi import APIRouter, Request
from pydantic import BaseModel

from wallet.errors import ApiError

router = APIRouter()


class HealthOut(BaseModel):
    status: Literal["ok"]


@router.get("/health", response_model=HealthOut)
async def health(request: Request) -> HealthOut:
    try:
        await request.app.state.pool.fetchval("SELECT 1")
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        raise ApiError(503, "db_unavailable", "Database is unavailable") from exc
    return HealthOut(status="ok")
