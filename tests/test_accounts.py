import pytest

from wallet.auth import hash_api_key
from wallet.cli import create_account

pytestmark = pytest.mark.anyio


async def test_me_returns_balances_as_strings(client, make_account):
    account_id, headers = await make_account("alice", balance_usd="9.85")
    response = await client.get("/v1/accounts/me", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "account_id": str(account_id),
        "name": "alice",
        "balance_usd": "9.85",
        "balance_credits": 0,
    }


@pytest.mark.parametrize(
    "headers", [{}, {"X-API-Key": "ll_not-a-real-key"}], ids=["missing", "wrong"]
)
async def test_me_requires_a_valid_api_key(client, headers):
    response = await client.get("/v1/accounts/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_create_account_stores_only_a_hash_of_the_key(app, client, settings):
    account_id, api_key = await create_account(settings.database_url, "bob")
    stored = await app.state.pool.fetchval(
        "SELECT api_key_hash FROM accounts WHERE id = $1", account_id
    )
    assert stored == hash_api_key(api_key)
    assert api_key not in stored
    response = await client.get("/v1/accounts/me", headers={"X-API-Key": api_key})
    assert response.json()["name"] == "bob"
