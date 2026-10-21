"""Payment provider contract: invoice calls out to the provider."""

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
