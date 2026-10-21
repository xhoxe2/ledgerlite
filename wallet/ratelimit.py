"""At most 30 POST requests a minute per API key."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request

from wallet.auth import current_account_id
from wallet.errors import ApiError

RATE_LIMIT_PER_MINUTE = 30


async def rate_limited_account_id(
    request: Request, account_id: Annotated[UUID, Depends(current_account_id)]
) -> UUID:
    # shortcut: a fixed one-minute window lets up to twice the limit through around a window
    # boundary; switch to a sliding window if that starts to matter.
    # shortcut: old windows are never deleted; add a cleanup job when the table grows.
    row = await request.app.state.pool.fetchrow(
        """
        INSERT INTO rate_limits (account_id, window_start, count)
        VALUES ($1, date_trunc('minute', now()), 1)
        ON CONFLICT (account_id, window_start) DO UPDATE SET count = rate_limits.count + 1
        RETURNING count,
                  ceil(extract(epoch FROM window_start + interval '1 minute' - now()))::int
                      AS seconds_left
        """,
        account_id,
    )
    if row["count"] > RATE_LIMIT_PER_MINUTE:
        raise ApiError(
            429,
            "rate_limited",
            "Too many POST requests, retry later",
            {"limit_per_minute": RATE_LIMIT_PER_MINUTE},
            headers={"Retry-After": str(max(row["seconds_left"], 1))},
        )
    return account_id
