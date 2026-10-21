"""POST /v1/webhooks/provider: the provider's signed event that closes a pending deposit."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from wallet import provider
from wallet.invoice_status import InvoiceStatus, apply_invoice_status
from wallet.money import NonNegativeMoney

router = APIRouter()


class ProviderEvent(BaseModel):
    event_id: str
    invoice_id: str
    status: InvoiceStatus
    paid_amount: NonNegativeMoney
    occurred_at: datetime


class WebhookResult(BaseModel):
    result: Literal["applied", "ignored"]


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
