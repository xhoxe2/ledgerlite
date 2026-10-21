"""Both real apps wired in-process: wallet -> provider mock -> signed webhook -> wallet."""

import httpx
import pytest

from provider_mock.app import create_app as create_provider_mock
from wallet.app import create_app

pytestmark = pytest.mark.anyio


async def test_a_deposit_travels_through_the_provider_mock_and_back_as_a_webhook(
    app, settings, make_account
):
    _, headers = await make_account()

    # The mock is built first, so it reaches the wallet through a callable that looks it up late.
    async def wallet_when_called(scope, receive, send):
        await wallet(scope, receive, send)

    provider_mock = create_provider_mock(
        api_key=settings.provider_api_key,
        webhook_secret=settings.webhook_secret,
        public_url="http://provider.test",
        webhook_transport=httpx.ASGITransport(app=wallet_when_called),
    )
    wallet = create_app(settings, provider_transport=httpx.ASGITransport(app=provider_mock))
    provider_headers = {"Authorization": "Bearer test-provider-key"}

    async with (
        wallet.router.lifespan_context(wallet),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=wallet), base_url="http://wallet.test"
        ) as wallet_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=provider_mock), base_url="http://provider.test"
        ) as provider_client,
    ):
        deposit = await wallet_client.post(
            "/v1/deposits", headers=headers, json={"amount": "10.00"}
        )
        assert deposit.status_code == 201
        invoice_id = deposit.json()["pay_url"].rsplit("/", 1)[-1]

        paid = await provider_client.post(
            f"/invoices/{invoice_id}/simulate",
            headers=provider_headers,
            json={"status": "partially_paid", "paid_amount": "7.50", "duplicate": True},
        )
        assert paid.json()["deliveries"] == [{"status_code": 200}, {"status_code": 200}]

        me = await wallet_client.get("/v1/accounts/me", headers=headers)
        assert me.json()["balance_usd"] == "7.39"
