"""Payment provider mock: in-memory invoices, a simulate endpoint and signed webhooks."""

import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import UTC, datetime
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

Amount = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?$")]
FINAL_STATUSES = {"paid", "partially_paid", "expired"}
WEBHOOK_TIMEOUT = httpx.Timeout(5.0, connect=2.0)


class InvoiceIn(BaseModel):
    amount: Amount
    currency: Literal["USD"]
    external_id: str
    callback_url: str


class SimulateIn(BaseModel):
    status: Literal["paid", "partially_paid", "expired"]
    paid_amount: Amount | None = None
    deliver_webhook: bool = True
    duplicate: bool = False


def sign(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"sha256={digest.hexdigest()}"


def create_app(
    api_key: str | None = None,
    webhook_secret: str | None = None,
    public_url: str | None = None,
    webhook_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    api_key = api_key or os.environ["PROVIDER_API_KEY"]
    webhook_secret = webhook_secret or os.environ["WEBHOOK_SECRET"]
    public_url = public_url or os.environ.get("PUBLIC_URL", "http://localhost:8001")
    invoices: dict[str, dict] = {}

    def require_api_key(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {api_key}".encode()
        if not authorization or not hmac.compare_digest(authorization.encode(), expected):
            raise HTTPException(401, "Invalid API key")

    def find_invoice(invoice_id: str) -> dict:
        if invoice_id not in invoices:
            raise HTTPException(404, "Invoice not found")
        return invoices[invoice_id]

    async def deliver_webhook(invoice: dict, copies: int) -> list[dict]:
        event = {
            "event_id": f"evt_{secrets.token_hex(8)}",
            "invoice_id": invoice["invoice_id"],
            "status": invoice["status"],
            "paid_amount": invoice["paid_amount"],
            "occurred_at": datetime.now(UTC).isoformat(),
        }
        body = json.dumps(event).encode()
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "X-Timestamp": timestamp,
            "X-Signature": sign(webhook_secret, timestamp, body),
        }
        deliveries = []
        async with httpx.AsyncClient(
            timeout=WEBHOOK_TIMEOUT, transport=webhook_transport
        ) as client:
            for _ in range(copies):
                try:
                    response = await client.post(
                        invoice["callback_url"], content=body, headers=headers
                    )
                    deliveries.append({"status_code": response.status_code})
                except httpx.HTTPError as exc:
                    deliveries.append({"error": type(exc).__name__})
        return deliveries

    app = FastAPI(title="Payment provider mock")
    # Every route but /health needs the bearer key.
    invoice_routes = APIRouter(dependencies=[Depends(require_api_key)])

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @invoice_routes.post("/invoices", status_code=201)
    async def create_invoice(body: InvoiceIn) -> dict:
        invoice_id = f"inv_{secrets.token_hex(8)}"
        invoices[invoice_id] = {
            "invoice_id": invoice_id,
            "status": "pending",
            "amount": body.amount,
            "paid_amount": "0.00",
            "currency": body.currency,
            "external_id": body.external_id,
            "callback_url": body.callback_url,
        }
        return {
            "invoice_id": invoice_id,
            "status": "pending",
            "pay_url": f"{public_url}/pay/{invoice_id}",
        }

    @invoice_routes.get("/invoices/{invoice_id}")
    async def get_invoice(invoice_id: str) -> dict:
        return find_invoice(invoice_id)

    @invoice_routes.post("/invoices/{invoice_id}/simulate")
    async def simulate(invoice_id: str, body: SimulateIn) -> dict:
        invoice = find_invoice(invoice_id)
        if invoice["status"] in FINAL_STATUSES:
            raise HTTPException(409, "Invoice is already final")
        if body.status == "partially_paid" and body.paid_amount is None:
            raise HTTPException(422, "paid_amount is required for partially_paid")
        default_paid = invoice["amount"] if body.status == "paid" else "0.00"
        invoice.update(status=body.status, paid_amount=body.paid_amount or default_paid)
        deliveries = []
        if body.deliver_webhook:
            deliveries = await deliver_webhook(invoice, copies=2 if body.duplicate else 1)
        return {"invoice": invoice, "deliveries": deliveries}

    # include_router copies the routes declared so far, so it comes after them.
    app.include_router(invoice_routes)
    return app
