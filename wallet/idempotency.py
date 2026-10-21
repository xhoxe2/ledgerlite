"""Idempotency keys: claim a key, refuse reuse with another body, replay the saved response."""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from wallet.errors import ApiError


@dataclass(frozen=True)
class StoredResponse:
    status_code: int
    body: str


def hash_request(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def claim(
    conn: asyncpg.Connection, account_id: UUID, key: str, request_hash: str
) -> StoredResponse | None:
    """Runs inside the caller's transaction. None means this request now owns the key."""
    # shortcut: keys are never deleted, add a TTL cleanup job when the table grows.
    claimed = await conn.fetchval(
        """
        INSERT INTO idempotency_keys (account_id, key, request_hash) VALUES ($1, $2, $3)
        ON CONFLICT DO NOTHING
        RETURNING true
        """,
        account_id,
        key,
        request_hash,
    )
    if claimed:
        return None
    # A parallel retry cannot get here before the first request commits: both lock the same
    # accounts (FOR UPDATE) before claiming, so the saved response is visible to this statement.
    row = await conn.fetchrow(
        """
        SELECT request_hash, status_code, response FROM idempotency_keys
        WHERE account_id = $1 AND key = $2
        """,
        account_id,
        key,
    )
    if row["request_hash"] != request_hash:
        raise ApiError(
            409, "idempotency_key_reused", "Idempotency-Key was already used with another body"
        )
    return StoredResponse(row["status_code"], row["response"])


async def save(
    conn: asyncpg.Connection, account_id: UUID, key: str, status_code: int, content: dict
) -> StoredResponse:
    body = json.dumps(content)
    await conn.execute(
        """
        UPDATE idempotency_keys SET status_code = $3, response = $4
        WHERE account_id = $1 AND key = $2
        """,
        account_id,
        key,
        status_code,
        body,
    )
    return StoredResponse(status_code, body)
