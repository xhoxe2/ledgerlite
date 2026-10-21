"""Wallet service application factory."""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from wallet import accounts, deposits
from wallet.config import Settings
from wallet.db import create_pool
from wallet.errors import install_error_handlers
from wallet.provider import new_provider_client


def create_app(
    settings: Settings | None = None,
    provider_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pool = await create_pool(settings.database_url)
        app.state.provider = new_provider_client(settings, provider_transport)
        try:
            yield
        finally:
            await app.state.provider.aclose()
            await app.state.pool.close()

    app = FastAPI(title="LedgerLite wallet", lifespan=lifespan)
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(accounts.router)
    app.include_router(deposits.router)
    return app
