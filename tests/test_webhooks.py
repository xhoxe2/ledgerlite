"""Provider webhooks: signature checks, crediting, duplicates and late events."""

import asyncio
import hashlib
import hmac
import json
import time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.anyio

EVENT = {
    "event_id": "evt_1",
    "invoice_id": "inv_1",
    "status": "paid",
    "paid_amount": "10.00",
    "occurred_at": "2026-10-21T12:00:00+00:00",
}


@pytest.fixture
def pending_deposit(app, make_account):
    async def create(amount: str = "10.00", invoice_id: str = "inv_1") -> UUID:
        account_id, _ = await make_account()
        await app.state.pool.execute(
            """
            INSERT INTO deposits (id, account_id, amount, status, invoice_id, pay_url)
            VALUES ($1, $2, $3, 'pending', $4, 'http://provider.test/pay')
            """,
            uuid4(),
            account_id,
            Decimal(amount),
            invoice_id,
        )
        return account_id

    return create


@pytest.fixture
def send_webhook(client, signed_webhook):
    async def send(secret=None, timestamp_offset=0, **event_fields):
        signing = {"timestamp_offset": timestamp_offset} | ({"secret": secret} if secret else {})
        body, headers = signed_webhook(EVENT | event_fields, **signing)
        return await client.post("/v1/webhooks/provider", content=body, headers=headers)

    return send


async def balance_of(app, account_id) -> Decimal:
    return await app.state.pool.fetchval(
        "SELECT balance_usd FROM accounts WHERE id = $1", account_id
    )


def signature_header(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"sha256={digest.hexdigest()}"


async def test_paid_webhook_credits_amount_minus_fee(app, pending_deposit, send_webhook):
    account_id = await pending_deposit()
    response = await send_webhook()
    assert (response.status_code, response.json()) == (200, {"result": "applied"})
    assert await balance_of(app, account_id) == Decimal("9.85")
    deposit = await app.state.pool.fetchrow(
        "SELECT status, paid_amount, fee, credited, closed_by_event_id FROM deposits"
    )
    assert dict(deposit) == {
        "status": "paid",
        "paid_amount": Decimal("10.00"),
        "fee": Decimal("0.15"),
        "credited": Decimal("9.85"),
        "closed_by_event_id": "evt_1",
    }


async def test_partially_paid_credits_what_was_paid_minus_fee(app, pending_deposit, send_webhook):
    account_id = await pending_deposit()
    await send_webhook(status="partially_paid", paid_amount="7.50")
    assert await balance_of(app, account_id) == Decimal("7.39")  # fee 0.1125 rounds to 0.11


async def test_partially_paid_with_nothing_paid_writes_no_ledger_entry(
    app, pending_deposit, send_webhook
):
    account_id = await pending_deposit()
    response = await send_webhook(status="partially_paid", paid_amount="0.00")
    assert (response.status_code, response.json()) == (200, {"result": "applied"})
    assert await app.state.pool.fetchval("SELECT count(*) FROM ledger_entries") == 0
    assert await balance_of(app, account_id) == Decimal("0.00")


async def test_expired_closes_the_deposit_and_later_events_change_nothing(
    app, pending_deposit, send_webhook
):
    account_id = await pending_deposit()
    expired = await send_webhook(status="expired", paid_amount="0.00")
    late = await send_webhook(status="paid", event_id="evt_2")
    assert (expired.json(), late.json()) == ({"result": "applied"}, {"result": "ignored"})
    assert await balance_of(app, account_id) == Decimal("0.00")
    assert await app.state.pool.fetchval("SELECT status FROM deposits") == "expired"


async def test_expired_never_credits_even_when_it_carries_a_paid_amount(
    app, pending_deposit, send_webhook
):
    account_id = await pending_deposit()
    await send_webhook(status="expired", paid_amount="10.00")
    assert await balance_of(app, account_id) == Decimal("0.00")
    deposit = await app.state.pool.fetchrow("SELECT status, credited FROM deposits")
    assert dict(deposit) == {"status": "expired", "credited": None}


async def test_repeated_event_is_credited_once(app, pending_deposit, send_webhook):
    account_id = await pending_deposit()
    first, second = await send_webhook(), await send_webhook()
    assert (first.json(), second.json()) == ({"result": "applied"}, {"result": "ignored"})
    assert await balance_of(app, account_id) == Decimal("9.85")


async def test_concurrent_duplicates_are_credited_once(app, pending_deposit, send_webhook):
    account_id = await pending_deposit()
    responses = await asyncio.gather(*(send_webhook() for _ in range(5)))
    assert sorted(r.json()["result"] for r in responses) == ["applied"] + ["ignored"] * 4
    assert await balance_of(app, account_id) == Decimal("9.85")
    assert await app.state.pool.fetchval("SELECT count(*) FROM ledger_entries") == 1


@pytest.mark.parametrize(
    "signing",
    [{"secret": "wrong-secret"}, {"timestamp_offset": -310}, {"timestamp_offset": 310}],
    ids=["wrong-secret", "expired", "from-the-future"],
)
async def test_bad_signature_is_401_and_credits_nothing(
    app, pending_deposit, send_webhook, signing
):
    account_id = await pending_deposit()
    response = await send_webhook(**signing)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"
    assert await balance_of(app, account_id) == Decimal("0.00")


async def test_timestamp_inside_the_window_is_accepted(pending_deposit, send_webhook):
    await pending_deposit()
    assert (await send_webhook(timestamp_offset=-290)).status_code == 200


async def test_missing_signature_headers_are_401(client, pending_deposit):
    await pending_deposit()
    response = await client.post("/v1/webhooks/provider", json=EVENT)
    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers_for",
    [
        lambda timestamp, sign: {"X-Timestamp": timestamp},
        lambda timestamp, sign: {"X-Signature": sign(timestamp)},
        lambda timestamp, sign: {"X-Timestamp": "abc", "X-Signature": sign("abc")},
        lambda timestamp, sign: {
            "X-Timestamp": timestamp,
            "X-Signature": sign(timestamp).removeprefix("sha256="),
        },
    ],
    ids=["timestamp-only", "signature-only", "non-numeric-timestamp", "no-sha256-prefix"],
)
async def test_malformed_signature_headers_are_401_and_credit_nothing(
    app, client, pending_deposit, settings, headers_for
):
    account_id = await pending_deposit()
    body = json.dumps(EVENT).encode()

    def sign(timestamp: str) -> str:
        return signature_header(settings.webhook_secret, timestamp, body)

    headers = headers_for(str(int(time.time())), sign)
    response = await client.post("/v1/webhooks/provider", content=body, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"
    assert await balance_of(app, account_id) == Decimal("0.00")


async def test_oversized_timestamp_is_401(client, pending_deposit, settings):
    await pending_deposit()
    body = json.dumps(EVENT).encode()
    timestamp = "1" + "0" * 400
    headers = {
        "X-Timestamp": timestamp,
        "X-Signature": signature_header(settings.webhook_secret, timestamp, body),
    }
    response = await client.post("/v1/webhooks/provider", content=body, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"


async def test_signature_covers_the_raw_bytes_not_a_reserialized_body(
    app, client, pending_deposit, signed_webhook
):
    account_id = await pending_deposit()
    body, headers = signed_webhook(json.dumps(EVENT, separators=(",", ":")).encode())
    response = await client.post("/v1/webhooks/provider", content=body, headers=headers)
    assert (response.status_code, response.json()) == (200, {"result": "applied"})
    assert await balance_of(app, account_id) == Decimal("9.85")


async def test_unsigned_malformed_json_is_401_not_400(client):
    response = await client.post("/v1/webhooks/provider", content=b"{not json")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"


async def test_tampered_body_is_401(client, pending_deposit, signed_webhook):
    await pending_deposit()
    body, headers = signed_webhook(EVENT | {"paid_amount": "1.00"})
    tampered = body.replace(b'"1.00"', b'"1000.00"')
    response = await client.post("/v1/webhooks/provider", content=tampered, headers=headers)
    assert response.status_code == 401


async def test_unknown_invoice_is_404(send_webhook):
    response = await send_webhook(invoice_id="inv_missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_invoice"


async def test_signed_but_invalid_event_is_422(pending_deposit, send_webhook):
    await pending_deposit()
    response = await send_webhook(status="refunded")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_signed_but_malformed_json_is_400(client, signed_webhook):
    body, headers = signed_webhook(b"{not json")
    response = await client.post("/v1/webhooks/provider", content=body, headers=headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_json"
