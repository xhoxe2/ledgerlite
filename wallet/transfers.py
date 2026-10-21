"""POST /v1/transfers: moves USD between accounts, made idempotent by Idempotency-Key."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel

from wallet import idempotency
from wallet.errors import ApiError
from wallet.ledger import post_entry
from wallet.money import PositiveMoney
from wallet.ratelimit import rate_limited_account_id

router = APIRouter()

MAX_IDEMPOTENCY_KEY_LENGTH = 255


class TransferIn(BaseModel):
    to_account_id: UUID
    amount: PositiveMoney


class TransferOut(BaseModel):
    transfer_id: UUID
    from_account_id: UUID
    to_account_id: UUID
    amount: Decimal


@router.post("/v1/transfers", status_code=201, response_model=TransferOut)
async def create_transfer(
    request: Request,
    body: TransferIn,
    account_id: Annotated[UUID, Depends(rate_limited_account_id)],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Response:
    if not idempotency_key or len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise ApiError(
            400, "invalid_idempotency_key", "Idempotency-Key header (1-255 chars) is required"
        )
    if body.to_account_id == account_id:
        raise ApiError(422, "same_account", "Cannot transfer to your own account")
    request_hash = idempotency.hash_request(
        {"to_account_id": str(body.to_account_id), "amount": str(body.amount)}
    )
    async with request.app.state.pool.acquire() as conn, conn.transaction():
        # Locks come before the claim: the key's foreign key takes a shared lock on the sender
        # row, so two transfers that claimed first would wait on each other's shared lock.
        locked = await _lock_accounts(conn, [account_id, body.to_account_id])
        stored = await idempotency.claim(conn, account_id, idempotency_key, request_hash)
        if stored is None:
            try:
                async with conn.transaction():  # savepoint: a business error leaves no writes
                    content = await _transfer(conn, account_id, body, locked)
                status_code = 201
            except ApiError as exc:
                status_code, content = exc.status_code, exc.body()
            stored = await idempotency.save(conn, account_id, idempotency_key, status_code, content)
    return Response(stored.body, status_code=stored.status_code, media_type="application/json")


async def _lock_accounts(conn: asyncpg.Connection, account_ids: list[UUID]) -> set[UUID]:
    # Rows are locked in id order, so opposite transfers (A->B, B->A) cannot deadlock.
    rows = await conn.fetch(
        "SELECT id FROM accounts WHERE id = ANY($1::uuid[]) ORDER BY id FOR UPDATE",
        account_ids,
    )
    return {row["id"] for row in rows}


async def _transfer(
    conn: asyncpg.Connection, sender_id: UUID, body: TransferIn, locked: set[UUID]
) -> dict:
    if body.to_account_id not in locked:
        raise ApiError(
            404,
            "account_not_found",
            "Recipient account does not exist",
            {"to_account_id": str(body.to_account_id)},
        )
    transfer_id = uuid4()
    await post_entry(
        conn,
        account_id=sender_id,
        entry_type="transfer_out",
        amount_usd=-body.amount,
        ref_id=transfer_id,
        counterparty_id=body.to_account_id,
    )
    await post_entry(
        conn,
        account_id=body.to_account_id,
        entry_type="transfer_in",
        amount_usd=body.amount,
        ref_id=transfer_id,
        counterparty_id=sender_id,
    )
    return TransferOut(
        transfer_id=transfer_id,
        from_account_id=sender_id,
        to_account_id=body.to_account_id,
        amount=body.amount,
    ).model_dump(mode="json")
