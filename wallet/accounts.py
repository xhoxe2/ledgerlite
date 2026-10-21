"""GET /v1/accounts/me: the caller's balances."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from wallet.auth import current_account_id

router = APIRouter()


class AccountOut(BaseModel):
    account_id: UUID
    name: str
    balance_usd: Decimal
    balance_credits: int


@router.get("/v1/accounts/me", response_model=AccountOut)
async def get_my_account(
    request: Request, account_id: Annotated[UUID, Depends(current_account_id)]
) -> AccountOut:
    row = await request.app.state.pool.fetchrow(
        "SELECT name, balance_usd, balance_credits FROM accounts WHERE id = $1", account_id
    )
    return AccountOut(account_id=account_id, **dict(row))
