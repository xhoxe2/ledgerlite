"""At most 30 POST requests in any 60 seconds per API key."""

import math
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request

from wallet.auth import current_account_id
from wallet.errors import ApiError

RATE_LIMIT_PER_MINUTE = 30
WINDOW = timedelta(minutes=1)


async def rate_limited_account_id(
    request: Request, account_id: Annotated[UUID, Depends(current_account_id)]
) -> UUID:
    async with request.app.state.pool.acquire() as conn, conn.transaction():
        # Serializes one account's requests, so two of them cannot both see 29 hits and both get
        # in. Not the accounts row: transfers lock it in their own order and must not wait here.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1::uuid::text, 0))", account_id
        )
        # Read after the lock: now() is when the transaction began, before any wait for it.
        now = await conn.fetchval("SELECT clock_timestamp()")
        await conn.execute(
            "DELETE FROM rate_limit_hits WHERE account_id = $1 AND at <= $2",
            account_id,
            now - WINDOW,
        )
        window = await conn.fetchrow(
            "SELECT count(*) AS hits, min(at) AS oldest FROM rate_limit_hits WHERE account_id = $1",
            account_id,
        )
        if window["hits"] < RATE_LIMIT_PER_MINUTE:
            await conn.execute(
                "INSERT INTO rate_limit_hits (account_id, at) VALUES ($1, $2)", account_id, now
            )
    # Raised after the commit, so a rejected request still clears the expired hits.
    if window["hits"] >= RATE_LIMIT_PER_MINUTE:
        seconds_left = math.ceil((window["oldest"] + WINDOW - now).total_seconds())
        raise ApiError(
            429,
            "rate_limited",
            "Too many POST requests, retry later",
            {"limit_per_minute": RATE_LIMIT_PER_MINUTE},
            headers={"Retry-After": str(max(seconds_left, 1))},
        )
    return account_id
