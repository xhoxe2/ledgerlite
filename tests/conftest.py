"""Shared fixtures: a wallet app on the test database and a way to create accounts."""

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from wallet.app import create_app
from wallet.auth import hash_api_key, new_api_key
from wallet.config import Settings

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://ledger:ledger@localhost:55432/ledger_test"
)
WEBHOOK_SECRET = "test-webhook-secret"

MakeAccount = Callable[..., Awaitable[tuple[UUID, dict[str, str]]]]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        provider_url="http://provider.test",
        provider_api_key="test-provider-key",
        webhook_secret=WEBHOOK_SECRET,
        callback_url="http://wallet.test/v1/webhooks/provider",
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        await application.state.pool.execute(
            "TRUNCATE accounts, deposits, ledger_entries, idempotency_keys, rate_limits"
        )
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://wallet.test") as http:
        yield http


@pytest.fixture
def make_account(app: FastAPI) -> MakeAccount:
    async def create(name: str = "alice", balance_usd: str = "0.00") -> tuple[UUID, dict[str, str]]:
        """Inserts an account; returns its id and the headers that authenticate as it."""
        api_key = new_api_key()
        account_id = uuid4()
        await app.state.pool.execute(
            "INSERT INTO accounts (id, name, api_key_hash, balance_usd) VALUES ($1, $2, $3, $4)",
            account_id,
            name,
            hash_api_key(api_key),
            Decimal(balance_usd),
        )
        return account_id, {"X-API-Key": api_key}

    return create
