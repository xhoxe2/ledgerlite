"""API keys: generation, hashing and the dependency that authenticates a request."""

import hashlib
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import Header, Request

from wallet.errors import ApiError


def new_api_key() -> str:
    return f"ll_{secrets.token_urlsafe(32)}"


def hash_api_key(api_key: str) -> str:
    # A random 256-bit key needs no slow hash, and sha256 lets us find the account by it.
    return hashlib.sha256(api_key.encode()).hexdigest()


async def current_account_id(
    request: Request, x_api_key: Annotated[str | None, Header()] = None
) -> UUID:
    if x_api_key:
        account_id = await request.app.state.pool.fetchval(
            "SELECT id FROM accounts WHERE api_key_hash = $1", hash_api_key(x_api_key)
        )
        if account_id is not None:
            return account_id
    raise ApiError(401, "unauthorized", "Missing or invalid X-API-Key")
