"""Provider webhooks: signature checks, crediting, duplicates and late events."""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from wallet.invoice_status import apply_invoice_status

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


async def closed_deposit(app) -> dict:
    row = await app.state.pool.fetchrow(
        "SELECT status, paid_amount, credited, closed_by_event_id FROM deposits"
    )
    return dict(row)


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


async def test_the_credit_is_a_deposit_ledger_entry_that_points_at_the_deposit(
    app, pending_deposit, send_webhook
):
    account_id = await pending_deposit()
    await send_webhook()
    deposit_id = await app.state.pool.fetchval("SELECT id FROM deposits")
    [entry] = await app.state.pool.fetch(
        "SELECT account_id, type, amount_usd, ref_id FROM ledger_entries"
    )
    assert dict(entry) == {
        "account_id": account_id,
        "type": "deposit",
        "amount_usd": Decimal("9.85"),
        "ref_id": deposit_id,
    }


@pytest.mark.parametrize(
    ("deposit", "paid", "credited"),
    [
        ("10.00", "12.00", "11.82"),
        ("10.00", "9.00", "8.86"),  # the fee 0.135 is a tie and rounds up to 0.14
        ("0.99", "0.99", "0.98"),
        ("33.33", "33.33", "32.83"),
    ],
    ids=["paid-more-than-asked", "paid-less-than-asked", "fee-rounds-down", "fee-rounds-up"],
)
async def test_the_credit_follows_the_paid_amount_not_the_deposit_amount(
    app, pending_deposit, send_webhook, deposit, paid, credited
):
    account_id = await pending_deposit(deposit)
    await send_webhook(paid_amount=paid)
    assert await balance_of(app, account_id) == Decimal(credited)


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


async def test_a_paid_deposit_ignores_later_partial_and_expired_events(
    app, pending_deposit, send_webhook
):
    account_id = await pending_deposit()
    await send_webhook()
    partial = await send_webhook(event_id="evt_2", status="partially_paid", paid_amount="3.00")
    expired = await send_webhook(event_id="evt_3", status="expired", paid_amount="0.00")
    assert (partial.json(), expired.json()) == ({"result": "ignored"}, {"result": "ignored"})
    assert await balance_of(app, account_id) == Decimal("9.85")
    assert await closed_deposit(app) == {
        "status": "paid",
        "paid_amount": Decimal("10.00"),
        "credited": Decimal("9.85"),
        "closed_by_event_id": "evt_1",
    }


async def test_a_partially_paid_deposit_ignores_a_later_paid_event(
    app, pending_deposit, send_webhook
):
    account_id = await pending_deposit()
    await send_webhook(status="partially_paid", paid_amount="7.50")
    late = await send_webhook(event_id="evt_2", status="paid", paid_amount="10.00")
    assert late.json() == {"result": "ignored"}
    assert await balance_of(app, account_id) == Decimal("7.39")
    assert await closed_deposit(app) == {
        "status": "partially_paid",
        "paid_amount": Decimal("7.50"),
        "credited": Decimal("7.39"),
        "closed_by_event_id": "evt_1",
    }


async def test_a_deposit_closed_without_an_event_ignores_webhooks(
    app, pending_deposit, send_webhook
):
    account_id = await pending_deposit()
    async with app.state.pool.acquire() as conn, conn.transaction():
        closed = await apply_invoice_status(
            conn,
            invoice_id="inv_1",
            status="paid",
            paid_amount=Decimal("10.00"),
            event_id=None,
        )
    late = await send_webhook()
    assert (closed, late.json()) == ("applied", {"result": "ignored"})
    assert await balance_of(app, account_id) == Decimal("9.85")
    assert await app.state.pool.fetchval("SELECT count(*) FROM ledger_entries") == 1


async def test_a_new_event_for_a_closed_deposit_is_ignored_and_logged(
    pending_deposit, send_webhook, caplog
):
    await pending_deposit()
    await send_webhook(status="expired", paid_amount="0.00")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="wallet.invoice_status"):
        late = await send_webhook(event_id="evt_2")
    assert late.json() == {"result": "ignored"}
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name == "wallet.invoice_status" and record.levelno == logging.WARNING
    ]
    assert any("inv_1" in message and "evt_2" in message for message in warnings), warnings


async def test_a_repeat_of_the_closing_event_is_ignored_without_a_warning(
    pending_deposit, send_webhook, caplog
):
    await pending_deposit()
    await send_webhook()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="wallet.invoice_status"):
        repeat = await send_webhook()
    assert repeat.json() == {"result": "ignored"}
    assert [r for r in caplog.records if r.name == "wallet.invoice_status"] == []


async def test_the_providers_own_webhook_after_a_reconcile_is_ignored_without_a_warning(
    app, pending_deposit, send_webhook, caplog
):
    account_id = await pending_deposit()
    async with app.state.pool.acquire() as conn, conn.transaction():
        await apply_invoice_status(
            conn,
            invoice_id="inv_1",
            status="paid",
            paid_amount=Decimal("10.00"),
            event_id=None,
        )
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="wallet.invoice_status"):
        late = await send_webhook()
    assert late.json() == {"result": "ignored"}
    assert [r for r in caplog.records if r.name == "wallet.invoice_status"] == []
    assert await balance_of(app, account_id) == Decimal("9.85")


async def test_a_status_that_is_not_final_is_refused_and_changes_nothing(app, pending_deposit):
    account_id = await pending_deposit()
    with pytest.raises(ValueError):
        async with app.state.pool.acquire() as conn, conn.transaction():
            await apply_invoice_status(
                conn,
                invoice_id="inv_1",
                status="pending",
                paid_amount=Decimal("5.00"),
                event_id="evt_x",
            )
    assert await balance_of(app, account_id) == Decimal("0.00")
    assert await app.state.pool.fetchval("SELECT status FROM deposits") == "pending"
    assert await app.state.pool.fetchval("SELECT count(*) FROM ledger_entries") == 0


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


async def test_the_timestamp_window_is_five_minutes(app, pending_deposit, send_webhook):
    account_id = await pending_deposit()
    too_old = await send_webhook(timestamp_offset=-301)
    assert too_old.status_code == 401
    assert too_old.json()["error"]["code"] == "invalid_signature"
    assert await balance_of(app, account_id) == Decimal("0.00")
    assert (await send_webhook(timestamp_offset=-299)).status_code == 200
    assert await balance_of(app, account_id) == Decimal("9.85")


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


async def test_unsigned_oversized_body_is_413_not_401(client):
    response = await client.post("/v1/webhooks/provider", content=b"x" * 65537)
    assert response.status_code == 413
    error = response.json()["error"]
    assert (error["code"], error["details"]) == ("payload_too_large", {"max_bytes": 65536})


async def test_oversized_body_without_content_length_is_413(client):
    body = b"x" * 65537

    async def in_chunks():
        for start in range(0, len(body), 4096):
            yield body[start : start + 4096]

    request = client.build_request("POST", "/v1/webhooks/provider", content=in_chunks())
    assert "Content-Length" not in request.headers  # so the limit cannot rely on the header
    response = await client.send(request)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def test_body_of_exactly_the_limit_reaches_the_signature_check(client):
    response = await client.post("/v1/webhooks/provider", content=b"x" * 65536)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"
