"""POST /v1/purchases: credits bought with the USD balance."""

from decimal import Decimal

import pytest

pytestmark = pytest.mark.anyio


async def test_purchase_debits_usd_and_adds_credits(app, client, make_account):
    _, headers = await make_account("alice", balance_usd="50.00")
    response = await client.post("/v1/purchases", headers=headers, json={"credits": 507})
    assert response.status_code == 201
    body = response.json()
    assert (body["credits"], body["price"], body["balance_usd"], body["balance_credits"]) == (
        507,
        "48.17",
        "1.83",
        507,
    )
    entry = await app.state.pool.fetchrow("SELECT type, amount_usd, credits FROM ledger_entries")
    assert dict(entry) == {"type": "purchase", "amount_usd": Decimal("-48.17"), "credits": 507}


async def test_purchase_without_enough_usd_is_409(app, client, make_account):
    account_id, headers = await make_account("alice", balance_usd="10.00")
    response = await client.post("/v1/purchases", headers=headers, json={"credits": 333})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "insufficient_funds"
    row = await app.state.pool.fetchrow(
        "SELECT balance_usd, balance_credits FROM accounts WHERE id = $1", account_id
    )
    assert (row["balance_usd"], row["balance_credits"]) == (Decimal("10.00"), 0)


@pytest.mark.parametrize("credits", [True, 507.0, "507", 0, -1, 1_000_000_001])
async def test_credits_must_be_a_positive_whole_number(client, make_account, credits):
    _, headers = await make_account("alice", balance_usd="50.00")
    response = await client.post("/v1/purchases", headers=headers, json={"credits": credits})
    assert response.status_code == 422
