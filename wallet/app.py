"""Wallet service application factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from wallet import accounts
from wallet.config import Settings
from wallet.db import create_pool
from wallet.errors import install_error_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pool = await create_pool(settings.database_url)
        try:
            yield
        finally:
            await app.state.pool.close()

    app = FastAPI(title="LedgerLite wallet", lifespan=lifespan)
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(accounts.router)
    return app
