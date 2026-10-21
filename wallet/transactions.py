"""GET /v1/transactions: the caller's history, newest first, with keyset pagination."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from wallet.auth import current_account_id

router = APIRouter()


class EntryOut(BaseModel):
    id: int
    type: str
    amount_usd: Decimal
    credits: int
    ref_id: UUID
    counterparty_id: UUID | None
    created_at: datetime


class EntriesPage(BaseModel):
    items: list[EntryOut]
    next_cursor: str | None


@router.get("/v1/transactions", response_model=EntriesPage)
async def list_transactions(
    request: Request,
    account_id: Annotated[UUID, Depends(current_account_id)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(pattern=r"^[0-9]{1,18}$")] = None,
) -> EntriesPage:
    # One extra row only tells us whether another page exists.
    rows = await request.app.state.pool.fetch(
        """
        SELECT id, type, amount_usd, credits, ref_id, counterparty_id, created_at
        FROM ledger_entries
        WHERE account_id = $1 AND ($2::bigint IS NULL OR id < $2)
        ORDER BY id DESC
        LIMIT $3
        """,
        account_id,
        int(cursor) if cursor else None,
        limit + 1,
    )
    page = rows[:limit]
    next_cursor = str(page[-1]["id"]) if len(rows) > limit else None
    return EntriesPage(items=[EntryOut(**dict(row)) for row in page], next_cursor=next_cursor)
