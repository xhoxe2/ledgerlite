from decimal import Decimal

import httpx
import pytest

from wallet import transfers

pytestmark = pytest.mark.anyio


@pytest.fixture
async def lenient_client(app):
    """Like `client`, but an unhandled error comes back as the 500 a real caller would see."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://wallet.test") as http:
        yield http


async def test_unknown_route_uses_the_error_format(client):
    response = await client.get("/v1/unknown")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Not Found", "details": {}}
    }


async def test_wrong_method_uses_the_error_format(client):
    response = await client.post("/v1/accounts/me")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


async def test_an_unexpected_error_is_a_500_in_the_error_format_and_stores_nothing(
    app, lenient_client, make_account, provider
):
    _, headers = await make_account()

    def explode(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("not an HTTP error")

    provider.respond = explode
    response = await lenient_client.post("/v1/deposits", headers=headers, json={"amount": "10.00"})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert await app.state.pool.fetchval("SELECT count(*) FROM deposits") == 0


async def test_a_500_is_not_cached_under_the_idempotency_key(
    app, lenient_client, make_account, monkeypatch
):
    alice, headers = await make_account("alice", balance_usd="10.00")
    bob, _ = await make_account("bob")
    request = {
        "headers": {**headers, "Idempotency-Key": "key-1"},
        "json": {"to_account_id": str(bob), "amount": "4.00"},
    }
    real_transfer = transfers._transfer

    async def explode(*args, **kwargs):
        raise RuntimeError("not an ApiError")

    monkeypatch.setattr(transfers, "_transfer", explode)
    failed = await lenient_client.post("/v1/transfers", **request)
    monkeypatch.setattr(transfers, "_transfer", real_transfer)
    retried = await lenient_client.post("/v1/transfers", **request)

    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "internal_error"
    assert retried.status_code == 201
    balances = await app.state.pool.fetch("SELECT id, balance_usd FROM accounts")
    assert {row["id"]: row["balance_usd"] for row in balances} == {
        alice: Decimal("6.00"),
        bob: Decimal("4.00"),
    }
