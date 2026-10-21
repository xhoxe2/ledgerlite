"""Shared fixtures: a wallet app on the test database and a way to create accounts."""

import hashlib
import hmac
import json
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal
from urllib.parse import urlparse
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
# The compose database publishes the real `ledger` database on the same port, and the fixtures
# TRUNCATE every table, so a test run must never be pointed at a database not meant for tests.
DATABASE_NAME = urlparse(TEST_DATABASE_URL).path.lstrip("/")
if not DATABASE_NAME.endswith("_test"):
    raise pytest.UsageError(
        f"TEST_DATABASE_URL must name a database ending in '_test', got {DATABASE_NAME!r}: "
        "the tests TRUNCATE its tables"
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


class FakeProvider:
    """Stands in for the payment provider; a test sets `respond` to script its answers."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.respond: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(
            500
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
async def app(settings: Settings, provider: FakeProvider) -> AsyncIterator[FastAPI]:
    application = create_app(settings, provider_transport=httpx.MockTransport(provider.handle))
    async with application.router.lifespan_context(application):
        await application.state.pool.execute(
            "TRUNCATE accounts, deposits, ledger_entries, idempotency_keys, rate_limit_hits"
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


@pytest.fixture
def signed_webhook() -> Callable[..., tuple[bytes, dict[str, str]]]:
    def build(
        payload: dict | bytes, *, secret: str = WEBHOOK_SECRET, timestamp_offset: int = 0
    ) -> tuple[bytes, dict[str, str]]:
        """Signs a webhook body the way the provider contract describes."""
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        timestamp = str(int(time.time()) + timestamp_offset)
        digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
        headers = {
            "Content-Type": "application/json",
            "X-Timestamp": timestamp,
            "X-Signature": f"sha256={digest.hexdigest()}",
        }
        return body, headers

    return build
