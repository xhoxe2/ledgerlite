"""Provider mock: invoices, final statuses and signed webhooks."""

import hashlib
import hmac
import json

import httpx
import pytest

from provider_mock.app import create_app

pytestmark = pytest.mark.anyio

API_KEY = "mock-key"
SECRET = "mock-secret"
AUTH = {"Authorization": f"Bearer {API_KEY}"}


def wallet_that_accepts(received: list[httpx.Request]) -> httpx.MockTransport:
    def receive(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200, json={"result": "applied"})

    return httpx.MockTransport(receive)


def mock_client(webhook_transport: httpx.MockTransport) -> httpx.AsyncClient:
    app = create_app(
        api_key=API_KEY,
        webhook_secret=SECRET,
        public_url="http://provider.test",
        webhook_transport=webhook_transport,
    )
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://provider.test")


@pytest.fixture
def webhooks() -> list[httpx.Request]:
    return []


@pytest.fixture
async def mock(webhooks):
    async with mock_client(wallet_that_accepts(webhooks)) as http:
        yield http


async def new_invoice(mock: httpx.AsyncClient, amount: str = "10.00") -> str:
    response = await mock.post(
        "/invoices",
        headers=AUTH,
        json={
            "amount": amount,
            "currency": "USD",
            "external_id": "dep-1",
            "callback_url": "http://wallet.test/hook",
        },
    )
    assert response.status_code == 201
    assert response.json()["pay_url"].startswith("http://provider.test/pay/inv_")
    return response.json()["invoice_id"]


async def simulate(mock: httpx.AsyncClient, invoice_id: str, **body) -> httpx.Response:
    return await mock.post(f"/invoices/{invoice_id}/simulate", headers=AUTH, json=body)


async def test_requests_need_the_bearer_key(mock):
    response = await mock.get("/invoices/inv_any", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


async def test_health_needs_no_auth(mock):
    response = await mock.get("/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


async def test_new_invoice_is_pending(mock):
    invoice_id = await new_invoice(mock)
    invoice = (await mock.get(f"/invoices/{invoice_id}", headers=AUTH)).json()
    assert (invoice["status"], invoice["paid_amount"]) == ("pending", "0.00")


async def test_paid_sends_one_signed_webhook(mock, webhooks):
    invoice_id = await new_invoice(mock)
    response = await simulate(mock, invoice_id, status="paid")
    assert response.json()["deliveries"] == [{"status_code": 200}]
    [hook] = webhooks
    timestamp = hook.headers["X-Timestamp"]
    digest = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + hook.content, hashlib.sha256)
    assert hook.headers["X-Signature"] == f"sha256={digest.hexdigest()}"
    event = json.loads(hook.content)
    assert event["event_id"].startswith("evt_")
    assert (event["invoice_id"], event["status"], event["paid_amount"]) == (
        invoice_id,
        "paid",
        "10.00",
    )


async def test_duplicate_sends_the_same_event_twice(mock, webhooks):
    invoice_id = await new_invoice(mock)
    await simulate(mock, invoice_id, status="partially_paid", paid_amount="7.50", duplicate=True)
    assert len(webhooks) == 2
    assert webhooks[0].content == webhooks[1].content


async def test_deliver_webhook_false_sends_nothing(mock, webhooks):
    invoice_id = await new_invoice(mock)
    response = await simulate(mock, invoice_id, status="expired", deliver_webhook=False)
    assert (response.status_code, response.json()["deliveries"]) == (200, [])
    assert webhooks == []


async def test_final_invoice_does_not_change(mock):
    invoice_id = await new_invoice(mock)
    await simulate(mock, invoice_id, status="expired", deliver_webhook=False)
    assert (await simulate(mock, invoice_id, status="paid")).status_code == 409
    invoice = (await mock.get(f"/invoices/{invoice_id}", headers=AUTH)).json()
    assert invoice["status"] == "expired"


async def test_partially_paid_needs_paid_amount(mock):
    invoice_id = await new_invoice(mock)
    assert (await simulate(mock, invoice_id, status="partially_paid")).status_code == 422


async def test_unreachable_wallet_is_reported_not_raised():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with mock_client(httpx.MockTransport(refuse)) as mock:
        invoice_id = await new_invoice(mock)
        response = await simulate(mock, invoice_id, status="paid")
    assert response.status_code == 200
    assert response.json()["deliveries"] == [{"error": "ConnectError"}]
