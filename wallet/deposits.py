"""POST /v1/deposits: an invoice at the provider, closed later by the provider's webhook."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from wallet import provider
from wallet.money import PositiveMoney
from wallet.ratelimit import rate_limited_account_id

router = APIRouter()


class DepositIn(BaseModel):
    amount: PositiveMoney


class DepositOut(BaseModel):
    deposit_id: UUID
    status: str
    amount: Decimal
    pay_url: str


@router.post("/v1/deposits", status_code=201, response_model=DepositOut)
async def create_deposit(
    request: Request,
    body: DepositIn,
    account_id: Annotated[UUID, Depends(rate_limited_account_id)],
) -> DepositOut:
    deposit_id = uuid4()
    # Called outside any DB transaction; the deposit is stored only once the invoice exists,
    # so a provider failure leaves nothing behind.
    invoice = await provider.create_invoice(
        request.app.state.provider,
        amount=body.amount,
        external_id=str(deposit_id),
        callback_url=request.app.state.settings.callback_url,
    )
    await request.app.state.pool.execute(
        """
        INSERT INTO deposits (id, account_id, amount, status, invoice_id, pay_url)
        VALUES ($1, $2, $3, 'pending', $4, $5)
        """,
        deposit_id,
        account_id,
        body.amount,
        invoice["invoice_id"],
        invoice["pay_url"],
    )
    return DepositOut(
        deposit_id=deposit_id, status="pending", amount=body.amount, pay_url=invoice["pay_url"]
    )
