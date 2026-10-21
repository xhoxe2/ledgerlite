"""POST /v1/deposits: invoice creation at the provider and its failure modes."""

import json
from uuid import UUID

import httpx
import pytest

from wallet.provider import new_provider_client

pytestmark = pytest.mark.anyio

INVOICE = {"invoice_id": "inv_1", "status": "pending", "pay_url": "http://provider.test/pay/inv_1"}


async def test_deposit_creates_an_invoice_and_stores_it_pending(
    app, client, make_account, provider
):
    account_id, headers = await make_account()
    provider.respond = lambda request: httpx.Response(201, json=INVOICE)
    response = await client.post("/v1/deposits", headers=headers, json={"amount": "10.00"})
    assert response.status_code == 201
    body = response.json()
    assert (body["status"], body["amount"], body["pay_url"]) == (
        "pending",
        "10.00",
        INVOICE["pay_url"],
    )
    [sent] = provider.requests
    assert sent.headers["Authorization"] == "Bearer test-provider-key"
    assert json.loads(sent.content) == {
        "amount": "10.00",
        "currency": "USD",
        "external_id": body["deposit_id"],
        "callback_url": "http://wallet.test/v1/webhooks/provider",
    }
    row = await app.state.pool.fetchrow(
        "SELECT account_id, status, invoice_id FROM deposits WHERE id = $1",
        UUID(body["deposit_id"]),
    )
    assert dict(row) == {"account_id": account_id, "status": "pending", "invoice_id": "inv_1"}


def failing_with(error_class):
    def fail(request: httpx.Request) -> httpx.Response:
        raise error_class("provider is down", request=request)

    return fail


@pytest.mark.parametrize(
    ("respond", "status", "code"),
    [
        (failing_with(httpx.ReadTimeout), 504, "provider_timeout"),
        (failing_with(httpx.ConnectError), 503, "provider_unavailable"),
        (lambda request: httpx.Response(500, json={"detail": "boom"}), 502, "provider_error"),
        (lambda request: httpx.Response(500, json=INVOICE), 502, "provider_error"),
        (lambda request: httpx.Response(201, json={"status": "pending"}), 502, "provider_error"),
    ],
    ids=["timeout", "unreachable", "provider-5xx", "provider-5xx-with-invoice", "missing-fields"],
)
async def test_provider_failure_is_a_clear_error_and_stores_nothing(
    app, client, make_account, provider, respond, status, code
):
    _, headers = await make_account()
    provider.respond = respond
    response = await client.post("/v1/deposits", headers=headers, json={"amount": "10.00"})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert await app.state.pool.fetchval("SELECT count(*) FROM deposits") == 0


async def test_provider_client_carries_the_timeouts(settings):
    async with new_provider_client(settings) as client:
        assert client.timeout == httpx.Timeout(5.0, connect=2.0)


async def test_amount_as_a_number_is_422(client, make_account):
    _, headers = await make_account()
    response = await client.post("/v1/deposits", headers=headers, json={"amount": 10.5})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_malformed_json_is_400(client, make_account):
    _, headers = await make_account()
    response = await client.post(
        "/v1/deposits", headers={**headers, "Content-Type": "application/json"}, content=b"{oops"
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_json"
