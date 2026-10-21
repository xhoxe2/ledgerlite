"""Rate limit: at most 30 POST requests a minute per API key."""

import pytest

pytestmark = pytest.mark.anyio


async def fill_current_window(app, account_id, count):
    # Seeding the counter keeps the tests away from a minute boundary in the middle of a burst.
    await app.state.pool.execute(
        """
        INSERT INTO rate_limits (account_id, window_start, count)
        VALUES ($1, date_trunc('minute', now()), $2)
        """,
        account_id,
        count,
    )


async def buy(client, headers):
    return await client.post("/v1/purchases", headers=headers, json={"credits": 1})


async def test_thirty_first_post_in_a_minute_is_429(app, client, make_account):
    account_id, headers = await make_account()
    await fill_current_window(app, account_id, 30)
    response = await buy(client, headers)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(response.headers["Retry-After"]) <= 60


async def test_thirtieth_post_still_passes(app, client, make_account):
    account_id, headers = await make_account()
    await fill_current_window(app, account_id, 29)
    assert (await buy(client, headers)).status_code == 409  # reached the endpoint: no USD


async def test_every_post_counts_even_when_it_fails(app, client, make_account):
    account_id, headers = await make_account()
    await buy(client, headers)
    await client.post("/v1/deposits", headers=headers, json={"amount": 10.5})
    counted = await app.state.pool.fetchval(
        "SELECT sum(count) FROM rate_limits WHERE account_id = $1", account_id
    )
    assert counted == 2


async def test_limit_is_per_api_key(app, client, make_account):
    alice, _ = await make_account("alice")
    _, bob_headers = await make_account("bob")
    await fill_current_window(app, alice, 30)
    assert (await buy(client, bob_headers)).status_code == 409


async def test_reads_are_not_limited(app, client, make_account):
    account_id, headers = await make_account()
    await fill_current_window(app, account_id, 30)
    assert (await client.get("/v1/accounts/me", headers=headers)).status_code == 200


async def test_provider_webhook_is_not_limited(client):
    responses = [await client.post("/v1/webhooks/provider", content=b"{}") for _ in range(31)]
    assert {r.status_code for r in responses} == {401}
