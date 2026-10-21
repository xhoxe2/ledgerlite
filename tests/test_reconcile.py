"""Reconciliation: pending deposits whose webhook never arrived."""

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from wallet.deposits import reconcile
from wallet.provider import new_provider_client

pytestmark = pytest.mark.anyio

PROVIDER_ANSWERS = {
    "/invoices/inv_paid": {"status": "paid", "paid_amount": "10.00"},
    "/invoices/inv_waiting": {"status": "pending", "paid_amount": "0.00"},
}


async def add_pending_deposit(app, account_id, invoice_id, minutes_ago):
    await app.state.pool.execute(
        """
        INSERT INTO deposits (id, account_id, amount, status, invoice_id, pay_url, created_at)
        VALUES ($1, $2, 10.00, 'pending', $3, 'http://provider.test/pay',
                now() - make_interval(mins => $4::int))
        """,
        uuid4(),
        account_id,
        invoice_id,
        minutes_ago,
    )


async def test_reconcile_settles_old_pending_deposits(app, settings, make_account):
    account_id, _ = await make_account()
    for invoice_id in ("inv_paid", "inv_waiting", "inv_broken"):
        await add_pending_deposit(app, account_id, invoice_id, minutes_ago=20)
    await add_pending_deposit(app, account_id, "inv_fresh", minutes_ago=1)
    asked = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path in PROVIDER_ANSWERS:
            return httpx.Response(200, json=PROVIDER_ANSWERS[request.url.path])
        return httpx.Response(500)

    async with new_provider_client(settings, httpx.MockTransport(answer)) as client:
        counts = await reconcile(app.state.pool, client, older_than_minutes=15)

    assert counts == {"resolved": 1, "pending": 1, "failed": 1}
    assert "/invoices/inv_fresh" not in asked
    assert await app.state.pool.fetchval("SELECT balance_usd FROM accounts") == Decimal("9.85")
    rows = await app.state.pool.fetch("SELECT invoice_id, status FROM deposits")
    assert {row["invoice_id"]: row["status"] for row in rows} == {
        "inv_paid": "paid",
        "inv_waiting": "pending",
        "inv_broken": "pending",
        "inv_fresh": "pending",
    }
