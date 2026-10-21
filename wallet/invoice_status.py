"""Final invoice statuses and the one transition that closes a pending deposit."""

import logging
from decimal import Decimal
from typing import Literal, get_args

import asyncpg

from wallet.errors import ApiError
from wallet.ledger import post_entry
from wallet.money import provider_fee

logger = logging.getLogger(__name__)

InvoiceStatus = Literal["paid", "partially_paid", "expired"]
FINAL_STATUSES = get_args(InvoiceStatus)


async def apply_invoice_status(
    conn: asyncpg.Connection,
    *,
    invoice_id: str,
    status: InvoiceStatus,
    paid_amount: Decimal,
    event_id: str | None,
) -> Literal["applied", "ignored"]:
    """Closes a pending deposit exactly once; the webhook and reconciliation both use it."""
    if status not in FINAL_STATUSES:
        raise ValueError(f"Not a final invoice status: {status!r}")
    deposit = await conn.fetchrow(
        "SELECT id, account_id, status, paid_amount, closed_by_event_id FROM deposits "
        "WHERE invoice_id = $1 FOR UPDATE",
        invoice_id,
    )
    if deposit is None:
        raise ApiError(
            404, "unknown_invoice", "No deposit for this invoice", {"invoice_id": invoice_id}
        )
    if deposit["status"] != "pending":
        # Silent when the late event only repeats what closed the deposit: a repeat, or the
        # provider's own webhook after a reconcile (which has no event id to compare).
        disagrees = status != deposit["status"] or (
            status != "expired" and paid_amount != deposit["paid_amount"]
        )
        if disagrees:
            logger.warning(
                "Ignored event %s (status %s) for invoice %s: deposit already closed by event %s",
                event_id,
                status,
                invoice_id,
                deposit["closed_by_event_id"],
            )
        return "ignored"
    paid = fee = credited = None
    if status != "expired":
        paid, fee = paid_amount, provider_fee(paid_amount)
        credited = paid - fee
        if credited > 0:
            await post_entry(
                conn,
                account_id=deposit["account_id"],
                entry_type="deposit",
                amount_usd=credited,
                ref_id=deposit["id"],
            )
    await conn.execute(
        """
        UPDATE deposits
        SET status = $2, paid_amount = $3, fee = $4, credited = $5,
            closed_by_event_id = $6, closed_at = now()
        WHERE id = $1
        """,
        deposit["id"],
        status,
        paid,
        fee,
        credited,
        event_id,
    )
    return "applied"
