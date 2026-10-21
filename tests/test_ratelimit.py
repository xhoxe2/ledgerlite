"""Rate limit: at most 30 POST requests in any 60 seconds per API key (a sliding window)."""

import asyncio

import pytest

pytestmark = pytest.mark.anyio


async def add_hits(app, account_id, count, seconds_ago):
    """Records `count` accepted POSTs of the account made `seconds_ago` seconds ago."""
    # Ages are taken from the database clock, the one the limiter reads, never from Python time.
    await app.state.pool.execute(
        """
        INSERT INTO rate_limit_hits (account_id, at)
        SELECT $1, clock_timestamp() - make_interval(secs => $3)
        FROM generate_series(1, $2)
        """,
        account_id,
        count,
        seconds_ago,
    )


async def hits_of(app, account_id):
    return await app.state.pool.fetchval(
        "SELECT count(*) FROM rate_limit_hits WHERE account_id = $1", account_id
    )


async def buy(client, headers):
    return await client.post("/v1/purchases", headers=headers, json={"credits": 1})


@pytest.mark.parametrize(
    ("path", "body", "extra_headers"),
    [
        ("/v1/deposits", {"amount": "10.00"}, {}),
        (
            "/v1/transfers",
            {"to_account_id": "00000000-0000-0000-0000-000000000001", "amount": "1.00"},
            {"Idempotency-Key": "key-1"},
        ),
        ("/v1/purchases", {"credits": 1}, {}),
    ],
    ids=["deposits", "transfers", "purchases"],
)
async def test_thirty_first_post_in_a_minute_is_429_on_every_post_route(
    app, client, make_account, path, body, extra_headers
):
    account_id, headers = await make_account()
    await add_hits(app, account_id, 30, seconds_ago=30)
    response = await client.post(path, headers=headers | extra_headers, json=body)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert response.json()["error"]["details"] == {"limit_per_minute": 30}
    assert 1 <= int(response.headers["Retry-After"]) <= 60


async def test_thirtieth_post_still_passes(app, client, make_account):
    account_id, headers = await make_account()
    await add_hits(app, account_id, 29, seconds_ago=30)
    assert (await buy(client, headers)).status_code == 409  # reached the endpoint: no USD


async def test_hits_older_than_a_minute_do_not_count(app, client, make_account):
    account_id, headers = await make_account()
    await add_hits(app, account_id, 30, seconds_ago=61)
    assert (await buy(client, headers)).status_code == 409


async def test_window_slides_across_a_minute_boundary(app, client, make_account):
    account_id, headers = await make_account()
    # Unless the clock is at second :59, these hits fall in the previous calendar minute, so a
    # fixed window would let the next POST through; a sliding one must not, whatever the second.
    await add_hits(app, account_id, 30, seconds_ago=59)
    assert (await buy(client, headers)).status_code == 429


async def test_retry_after_counts_down_to_when_the_oldest_hit_leaves_the_window(
    app, client, make_account
):
    account_id, headers = await make_account()
    await add_hits(app, account_id, 30, seconds_ago=50)
    response = await buy(client, headers)
    assert response.status_code == 429
    assert 9 <= int(response.headers["Retry-After"]) <= 11


async def test_every_post_counts_even_when_it_fails(app, client, make_account):
    account_id, headers = await make_account()
    await buy(client, headers)
    await client.post("/v1/deposits", headers=headers, json={"amount": 10.5})
    assert await hits_of(app, account_id) == 2


async def test_limit_is_per_api_key(app, client, make_account):
    alice, _ = await make_account("alice")
    _, bob_headers = await make_account("bob")
    await add_hits(app, alice, 30, seconds_ago=30)
    assert (await buy(client, bob_headers)).status_code == 409


async def test_reads_are_not_limited(app, client, make_account):
    account_id, headers = await make_account()
    await add_hits(app, account_id, 30, seconds_ago=30)
    assert (await client.get("/v1/accounts/me", headers=headers)).status_code == 200


async def test_provider_webhook_is_not_limited(client):
    responses = [await client.post("/v1/webhooks/provider", content=b"{}") for _ in range(31)]
    assert {r.status_code for r in responses} == {401}
    # A 401 from the limiter's API-key check would pass the line above; this one is the signature's.
    assert {r.json()["error"]["code"] for r in responses} == {"invalid_signature"}


async def test_parallel_posts_cannot_slip_past_the_limit(client, make_account):
    _, headers = await make_account()
    responses = await asyncio.gather(*(buy(client, headers) for _ in range(40)))
    assert sorted(r.status_code for r in responses) == [409] * 30 + [429] * 10


async def test_rejected_post_is_not_recorded(app, client, make_account):
    # Otherwise a client that keeps retrying keeps its own window full and never gets out.
    account_id, headers = await make_account()
    await add_hits(app, account_id, 30, seconds_ago=50)
    assert (await buy(client, headers)).status_code == 429
    assert await hits_of(app, account_id) == 30


async def test_expired_hits_are_cleaned_up(app, client, make_account):
    account_id, headers = await make_account()
    await add_hits(app, account_id, 5, seconds_ago=120)
    await buy(client, headers)
    assert await hits_of(app, account_id) == 1
