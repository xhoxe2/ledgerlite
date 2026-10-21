"""The only place that changes a balance; each change is recorded in the same transaction."""

from decimal import Decimal
from uuid import UUID

import asyncpg

from wallet.errors import ApiError


async def post_entry(
    conn: asyncpg.Connection,
    *,
    account_id: UUID,
    entry_type: str,
    amount_usd: Decimal,
    ref_id: UUID,
    credits: int = 0,
    counterparty_id: UUID | None = None,
) -> asyncpg.Record:
    """Must run inside a transaction; callers make sure the account exists."""
    balances = await conn.fetchrow(
        """
        UPDATE accounts
        SET balance_usd = balance_usd + $2, balance_credits = balance_credits + $3
        WHERE id = $1 AND balance_usd + $2 >= 0
        RETURNING balance_usd, balance_credits
        """,
        account_id,
        amount_usd,
        credits,
    )
    if balances is None:
        raise ApiError(409, "insufficient_funds", "Not enough USD balance")
    await conn.execute(
        """
        INSERT INTO ledger_entries (account_id, type, amount_usd, credits, ref_id, counterparty_id)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        account_id,
        entry_type,
        amount_usd,
        credits,
        ref_id,
        counterparty_id,
    )
    return balances
