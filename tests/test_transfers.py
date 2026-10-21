"""Transfers: idempotency, insufficient funds and concurrency."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


async def transfer(client, headers, to_account_id, amount, key="key-1"):
    return await client.post(
        "/v1/transfers",
        headers={**headers, "Idempotency-Key": key},
        json={"to_account_id": str(to_account_id), "amount": amount},
    )


async def balance_of(app, account_id) -> Decimal:
    return await app.state.pool.fetchval(
        "SELECT balance_usd FROM accounts WHERE id = $1", account_id
    )


async def test_transfer_moves_money_and_records_both_legs(app, client, make_account):
    alice, headers = await make_account("alice", balance_usd="10.00")
    bob, _ = await make_account("bob")
    response = await transfer(client, headers, bob, "4.00")
    assert response.status_code == 201
    body = response.json()
    assert (body["from_account_id"], body["to_account_id"], body["amount"]) == (
        str(alice),
        str(bob),
        "4.00",
    )
    assert await balance_of(app, alice) == Decimal("6.00")
    assert await balance_of(app, bob) == Decimal("4.00")
    legs = await app.state.pool.fetch(
        """
        SELECT account_id, type, amount_usd, counterparty_id, ref_id
        FROM ledger_entries ORDER BY id
        """
    )
    assert [
        (leg["account_id"], leg["type"], leg["amount_usd"], leg["counterparty_id"]) for leg in legs
    ] == [
        (alice, "transfer_out", Decimal("-4.00"), bob),
        (bob, "transfer_in", Decimal("4.00"), alice),
    ]
    assert {str(leg["ref_id"]) for leg in legs} == {body["transfer_id"]}


async def test_same_key_and_body_replays_the_exact_response(app, client, make_account):
    alice, headers = await make_account("alice", balance_usd="10.00")
    bob, _ = await make_account("bob")
    first = await transfer(client, headers, bob, "4.00")
    second = await transfer(client, headers, bob, "4.0")  # the same amount, written differently
    assert (second.status_code, second.content) == (first.status_code, first.content)
    assert await balance_of(app, alice) == Decimal("6.00")


async def test_same_key_with_another_body_is_409(app, client, make_account):
    alice, headers = await make_account("alice", balance_usd="10.00")
    bob, _ = await make_account("bob")
    await transfer(client, headers, bob, "4.00")
    response = await transfer(client, headers, bob, "5.00")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_key_reused"
    assert await balance_of(app, alice) == Decimal("6.00")


async def test_same_key_with_another_recipient_is_409_and_the_original_still_replays(
    app, client, make_account
):
    alice, headers = await make_account("alice", balance_usd="10.00")
    bob, _ = await make_account("bob")
    carol, _ = await make_account("carol")
    first = await transfer(client, headers, bob, "4.00")
    reused = await transfer(client, headers, carol, "4.00")
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "idempotency_key_reused"
    assert await balance_of(app, alice) == Decimal("6.00")
    assert await balance_of(app, bob) == Decimal("4.00")
    assert await balance_of(app, carol) == Decimal("0.00")
    replay = await transfer(client, headers, bob, "4.00")
    assert (replay.status_code, replay.content) == (first.status_code, first.content)
    assert await app.state.pool.fetchval("SELECT count(*) FROM ledger_entries") == 2


async def test_keys_are_scoped_per_account(client, make_account):
    _, alice_headers = await make_account("alice", balance_usd="10.00")
    _, bob_headers = await make_account("bob", balance_usd="10.00")
    carol, _ = await make_account("carol")
    await transfer(client, alice_headers, carol, "1.00", key="shared")
    response = await transfer(client, bob_headers, carol, "2.00", key="shared")
    assert response.status_code == 201


async def test_missing_idempotency_key_is_400(client, make_account):
    _, headers = await make_account("alice", balance_usd="10.00")
    bob, _ = await make_account("bob")
    response = await client.post(
        "/v1/transfers", headers=headers, json={"to_account_id": str(bob), "amount": "1.00"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_idempotency_key"


@pytest.mark.parametrize("key", ["", "k" * 256], ids=["empty", "256-chars"])
async def test_an_idempotency_key_outside_1_to_255_chars_is_400(app, client, make_account, key):
    alice, headers = await make_account("alice", balance_usd="10.00")
    bob, _ = await make_account("bob")
    response = await transfer(client, headers, bob, "1.00", key=key)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_idempotency_key"
    assert await balance_of(app, alice) == Decimal("10.00")


async def test_an_idempotency_key_of_255_chars_is_accepted(client, make_account):
    _, headers = await make_account("alice", balance_usd="10.00")
    bob, _ = await make_account("bob")
    response = await transfer(client, headers, bob, "1.00", key="k" * 255)
    assert response.status_code == 201


async def test_insufficient_funds_is_409_and_replays_the_same_error(app, client, make_account):
    alice, headers = await make_account("alice", balance_usd="3.00")
    bob, _ = await make_account("bob")
    first = await transfer(client, headers, bob, "5.00")
    assert first.status_code == 409
    assert first.json()["error"]["code"] == "insufficient_funds"
    await app.state.pool.execute("UPDATE accounts SET balance_usd = 100 WHERE id = $1", alice)
    replay = await transfer(client, headers, bob, "5.00")
    assert (replay.status_code, replay.content) == (first.status_code, first.content)
    assert await balance_of(app, bob) == Decimal("0.00")


async def test_unknown_recipient_is_404(client, make_account):
    _, headers = await make_account("alice", balance_usd="10.00")
    response = await transfer(client, headers, uuid4(), "1.00")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "account_not_found"


async def test_unknown_recipient_replays_the_same_404_even_after_it_exists(
    app, client, make_account
):
    alice, headers = await make_account("alice", balance_usd="10.00")
    recipient = uuid4()
    first = await transfer(client, headers, recipient, "1.00")
    assert first.status_code == 404
    assert first.json()["error"]["code"] == "account_not_found"
    await app.state.pool.execute(
        "INSERT INTO accounts (id, name, api_key_hash) VALUES ($1, 'bob', 'hash-of-bobs-key')",
        recipient,
    )
    replay = await transfer(client, headers, recipient, "1.00")
    assert (replay.status_code, replay.content) == (first.status_code, first.content)
    assert await balance_of(app, alice) == Decimal("10.00")
    assert await balance_of(app, recipient) == Decimal("0.00")


async def test_transfer_to_self_is_422(client, make_account):
    alice, headers = await make_account("alice", balance_usd="10.00")
    response = await transfer(client, headers, alice, "1.00")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "same_account"


@pytest.mark.parametrize("amount", ["0.00", "0.001"])
async def test_amount_below_one_cent_is_422(client, make_account, amount):
    _, headers = await make_account("alice", balance_usd="10.00")
    bob, _ = await make_account("bob")
    response = await transfer(client, headers, bob, amount)
    assert response.status_code == 422


async def test_parallel_transfers_never_overdraw(app, client, make_account):
    alice, headers = await make_account("alice", balance_usd="100.00")
    bob, _ = await make_account("bob")
    responses = await asyncio.gather(
        *(transfer(client, headers, bob, "10.00", key=f"k{i}") for i in range(20))
    )
    assert sorted(r.status_code for r in responses) == [201] * 10 + [409] * 10
    assert await balance_of(app, alice) == Decimal("0.00")
    assert await balance_of(app, bob) == Decimal("100.00")


async def test_parallel_retries_with_one_key_debit_once(app, client, make_account):
    alice, headers = await make_account("alice", balance_usd="10.00")
    bob, _ = await make_account("bob")
    responses = await asyncio.gather(
        *(transfer(client, headers, bob, "4.00", key="same") for _ in range(5))
    )
    assert {r.status_code for r in responses} == {201}
    assert len({r.content for r in responses}) == 1
    assert await balance_of(app, alice) == Decimal("6.00")


async def test_opposite_transfers_do_not_deadlock(app, client, make_account):
    alice, alice_headers = await make_account("alice", balance_usd="50.00")
    bob, bob_headers = await make_account("bob", balance_usd="50.00")
    calls = [transfer(client, alice_headers, bob, "1.00", key=f"a{i}") for i in range(10)]
    calls += [transfer(client, bob_headers, alice, "1.00", key=f"b{i}") for i in range(10)]
    responses = await asyncio.gather(*calls)
    assert {r.status_code for r in responses} == {201}
    assert await balance_of(app, alice) == Decimal("50.00")
    assert await balance_of(app, bob) == Decimal("50.00")
