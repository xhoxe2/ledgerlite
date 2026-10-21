"""Deposits: an invoice at the provider, closed later by the provider's webhook."""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, get_args
from uuid import UUID, uuid4

import asyncpg
import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from wallet import provider
from wallet.auth import current_account_id
from wallet.errors import ApiError
from wallet.ledger import post_entry
from wallet.money import NonNegativeMoney, PositiveMoney, parse_amount, provider_fee

router = APIRouter()
logger = logging.getLogger(__name__)

InvoiceStatus = Literal["paid", "partially_paid", "expired"]
FINAL_STATUSES = get_args(InvoiceStatus)


class DepositIn(BaseModel):
    amount: PositiveMoney


class DepositOut(BaseModel):
    deposit_id: UUID
    status: str
    amount: Decimal
    pay_url: str


class ProviderEvent(BaseModel):
    event_id: str
    invoice_id: str
    status: InvoiceStatus
    paid_amount: NonNegativeMoney
    occurred_at: datetime


class WebhookResult(BaseModel):
    result: Literal["applied", "ignored"]


@router.post("/v1/deposits", status_code=201, response_model=DepositOut)
async def create_deposit(
    request: Request,
    body: DepositIn,
    account_id: Annotated[UUID, Depends(current_account_id)],
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


@router.post("/v1/webhooks/provider", response_model=WebhookResult)
async def provider_webhook(request: Request) -> WebhookResult:
    # The signature covers the raw bytes, so it is checked before the body is parsed.
    raw_body = await request.body()
    provider.verify_signature(
        request.app.state.settings.webhook_secret,
        raw_body,
        timestamp=request.headers.get("X-Timestamp"),
        signature=request.headers.get("X-Signature"),
    )
    try:
        event = ProviderEvent.model_validate_json(raw_body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    async with request.app.state.pool.acquire() as conn, conn.transaction():
        result = await apply_invoice_status(
            conn,
            invoice_id=event.invoice_id,
            status=event.status,
            paid_amount=event.paid_amount,
            event_id=event.event_id,
        )
    return WebhookResult(result=result)


async def apply_invoice_status(
    conn: asyncpg.Connection,
    *,
    invoice_id: str,
    status: InvoiceStatus,
    paid_amount: Decimal,
    event_id: str | None,
) -> Literal["applied", "ignored"]:
    """Closes a pending deposit exactly once; the webhook and reconciliation both use it."""
    deposit = await conn.fetchrow(
        "SELECT id, account_id, status FROM deposits WHERE invoice_id = $1 FOR UPDATE",
        invoice_id,
    )
    if deposit is None:
        raise ApiError(
            404, "unknown_invoice", "No deposit for this invoice", {"invoice_id": invoice_id}
        )
    if deposit["status"] != "pending":
        return "ignored"
    paid = fee = credited = None
    if status != "expired":
        paid, fee = paid_amount, provider_fee(paid_amount)
        credited = paid - fee
        if credited > 0:
            await post_entry(
                conn,
                account_id=deposit["account_id"],
                entry_type="deposit",
                amount_usd=credited,
                ref_id=deposit["id"],
            )
    await conn.execute(
        """
        UPDATE deposits
        SET status = $2, paid_amount = $3, fee = $4, credited = $5,
            closed_by_event_id = $6, closed_at = now()
        WHERE id = $1
        """,
        deposit["id"],
        status,
        paid,
        fee,
        credited,
        event_id,
    )
    return "applied"


async def reconcile(
    pool: asyncpg.Pool, client: httpx.AsyncClient, older_than_minutes: int
) -> dict[str, int]:
    """Asks the provider about deposits still pending after N minutes: their webhook was lost."""
    rows = await pool.fetch(
        """
        SELECT invoice_id FROM deposits
        WHERE status = 'pending' AND created_at < now() - make_interval(mins => $1::int)
        ORDER BY created_at
        """,
        older_than_minutes,
    )
    counts = {"resolved": 0, "pending": 0, "failed": 0}
    for row in rows:
        invoice_id = row["invoice_id"]
        try:
            invoice = await provider.get_invoice(client, invoice_id)
            if invoice["status"] not in FINAL_STATUSES:
                counts["pending"] += 1
                continue
            async with pool.acquire() as conn, conn.transaction():
                await apply_invoice_status(
                    conn,
                    invoice_id=invoice_id,
                    status=invoice["status"],
                    paid_amount=parse_amount(invoice["paid_amount"]),
                    event_id=None,
                )
            counts["resolved"] += 1
        except Exception:  # one bad invoice must not stop the rest; it is logged and counted
            logger.exception("Reconciliation failed for invoice %s", invoice_id)
            counts["failed"] += 1
    return counts
