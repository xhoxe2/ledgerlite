"""POST /v1/purchases: buys credits with the USD balance."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, StrictInt

from wallet.auth import current_account_id
from wallet.ledger import post_entry
from wallet.money import credits_price

router = APIRouter()

# Keeps the number within BIGINT: a bigger value would fail in the database as a 500.
MAX_CREDITS_PER_PURCHASE = 1_000_000_000


class PurchaseIn(BaseModel):
    credits: Annotated[StrictInt, Field(ge=1, le=MAX_CREDITS_PER_PURCHASE)]


class PurchaseOut(BaseModel):
    purchase_id: UUID
    credits: int
    price: Decimal
    balance_usd: Decimal
    balance_credits: int


@router.post("/v1/purchases", status_code=201, response_model=PurchaseOut)
async def create_purchase(
    request: Request,
    body: PurchaseIn,
    account_id: Annotated[UUID, Depends(current_account_id)],
) -> PurchaseOut:
    price = credits_price(body.credits)
    purchase_id = uuid4()
    async with request.app.state.pool.acquire() as conn, conn.transaction():
        balances = await post_entry(
            conn,
            account_id=account_id,
            entry_type="purchase",
            amount_usd=-price,
            credits=body.credits,
            ref_id=purchase_id,
        )
    return PurchaseOut(purchase_id=purchase_id, credits=body.credits, price=price, **dict(balances))
