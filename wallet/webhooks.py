"""POST /v1/webhooks/provider: the provider's signed event that closes a pending deposit."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from wallet import provider
from wallet.errors import ApiError
from wallet.invoice_status import InvoiceStatus, apply_invoice_status
from wallet.money import NonNegativeMoney

MAX_WEBHOOK_BODY_BYTES = 64 * 1024  # provider events are ~200 bytes

router = APIRouter()


class ProviderEvent(BaseModel):
    event_id: str
    invoice_id: str
    status: InvoiceStatus
    paid_amount: NonNegativeMoney
    occurred_at: datetime


class WebhookResult(BaseModel):
    result: Literal["applied", "ignored"]


async def _read_limited_body(request: Request) -> bytes:
    # Counts the bytes as they arrive: a chunked body has no Content-Length to check.
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_WEBHOOK_BODY_BYTES:
            raise ApiError(
                413,
                "payload_too_large",
                "Webhook body is too large",
                {"max_bytes": MAX_WEBHOOK_BODY_BYTES},
            )
        body += chunk
    return bytes(body)


@router.post("/v1/webhooks/provider", response_model=WebhookResult)
async def provider_webhook(request: Request) -> WebhookResult:
    # The signature covers the raw bytes, so it is checked before the body is parsed;
    # the size limit comes first because checking the signature needs the whole body.
    raw_body = await _read_limited_body(request)
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
