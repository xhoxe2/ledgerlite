"""GET /v1/transactions: own history, newest first, with keyset pagination."""

import pytest

pytestmark = pytest.mark.anyio


async def buy_one_credit(client, headers):
    response = await client.post("/v1/purchases", headers=headers, json={"credits": 1})
    assert response.status_code == 201


async def history(client, headers, **params):
    response = await client.get("/v1/transactions", headers=headers, params=params)
    assert response.status_code == 200
    return response.json()


async def test_history_is_paginated_newest_first(client, make_account):
    _, headers = await make_account("alice", balance_usd="1.00")
    for _ in range(3):
        await buy_one_credit(client, headers)
    first = await history(client, headers, limit=2)
    assert [item["amount_usd"] for item in first["items"]] == ["-0.10", "-0.10"]
    assert first["items"][0]["id"] > first["items"][1]["id"]
    second = await history(client, headers, limit=2, cursor=first["next_cursor"])
    assert len(second["items"]) == 1
    assert second["items"][0]["id"] < first["items"][1]["id"]
    assert second["next_cursor"] is None


async def test_history_shows_only_own_entries(client, make_account):
    _, alice = await make_account("alice", balance_usd="1.00")
    _, bob = await make_account("bob", balance_usd="1.00")
    await buy_one_credit(client, bob)
    assert await history(client, alice) == {"items": [], "next_cursor": None}


@pytest.mark.parametrize("params", [{"cursor": "abc"}, {"limit": 0}, {"limit": 101}])
async def test_bad_paging_parameters_are_422(client, make_account, params):
    _, headers = await make_account()
    response = await client.get("/v1/transactions", headers=headers, params=params)
    assert response.status_code == 422
