"""Payment provider contract: invoice calls out, webhook signatures in."""

import hashlib
import hmac
import time
from decimal import Decimal

import httpx

from wallet.config import Settings
from wallet.errors import ApiError

PROVIDER_TIMEOUT = httpx.Timeout(5.0, connect=2.0)


def new_provider_client(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.provider_url,
        headers={"Authorization": f"Bearer {settings.provider_api_key}"},
        timeout=PROVIDER_TIMEOUT,
        transport=transport,
    )


async def create_invoice(
    client: httpx.AsyncClient, *, amount: Decimal, external_id: str, callback_url: str
) -> dict:
    payload = {
        "amount": str(amount),
        "currency": "USD",
        "external_id": external_id,
        "callback_url": callback_url,
    }
    return await _call(
        client, "POST", "/invoices", json=payload, required=("invoice_id", "pay_url")
    )


async def _call(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    required: tuple[str, ...],
    json: dict | None = None,
) -> dict:
    try:
        response = await client.request(method, url, json=json)
    except httpx.TimeoutException as exc:  # a RequestError subclass, so it is caught first
        raise ApiError(504, "provider_timeout", "Payment provider did not respond in time") from exc
    except httpx.RequestError as exc:
        raise ApiError(503, "provider_unavailable", "Payment provider is unavailable") from exc
    if response.is_error:
        raise ApiError(
            502,
            "provider_error",
            "Payment provider returned an error",
            {"provider_status": response.status_code},
        )
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or not all(isinstance(payload.get(k), str) for k in required):
        raise ApiError(502, "provider_error", "Payment provider returned an unexpected response")
    return payload


SIGNATURE_TOLERANCE_SECONDS = 300


def verify_signature(
    secret: str, raw_body: bytes, *, timestamp: str | None, signature: str | None
) -> None:
    """Raises 401 unless the body carries our signature and a timestamp within 5 minutes of now."""
    if not timestamp or not signature or not signature.startswith("sha256="):
        raise _invalid_signature()
    try:
        sent_at = int(timestamp)
    except ValueError:
        raise _invalid_signature() from None
    if abs(time.time() - sent_at) > SIGNATURE_TOLERANCE_SECONDS:
        raise _invalid_signature()
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256)
    # Bytes, not str: compare_digest raises TypeError on str with non-ASCII characters.
    if not hmac.compare_digest(
        expected.hexdigest().encode(), signature.removeprefix("sha256=").encode()
    ):
        raise _invalid_signature()


def _invalid_signature() -> ApiError:
    return ApiError(401, "invalid_signature", "Webhook signature is missing, stale or wrong")
