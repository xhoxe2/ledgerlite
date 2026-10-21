"""Reconciliation: asks the provider about pending deposits whose webhook was lost."""

import logging

import asyncpg
import httpx

from wallet import provider
from wallet.invoice_status import FINAL_STATUSES, apply_invoice_status
from wallet.money import parse_amount

logger = logging.getLogger(__name__)


async def reconcile(
    pool: asyncpg.Pool, client: httpx.AsyncClient, older_than_minutes: int
) -> dict[str, int]:
    """Asks the provider about deposits still pending after N minutes: their webhook was lost."""
    rows = await pool.fetch(
        """
        SELECT invoice_id FROM deposits
        WHERE status = 'pending' AND created_at < now() - make_interval(mins => $1::int)
        ORDER BY created_at
        """,
        older_than_minutes,
    )
    counts = {"resolved": 0, "pending": 0, "failed": 0}
    for row in rows:
        invoice_id = row["invoice_id"]
        try:
            invoice = await provider.get_invoice(client, invoice_id)
            if invoice["status"] not in FINAL_STATUSES:
                counts["pending"] += 1
                continue
            async with pool.acquire() as conn, conn.transaction():
                await apply_invoice_status(
                    conn,
                    invoice_id=invoice_id,
                    status=invoice["status"],
                    paid_amount=parse_amount(invoice["paid_amount"]),
                    event_id=None,
                )
            counts["resolved"] += 1
        except Exception:  # one bad invoice must not stop the rest; it is logged and counted
            logger.exception("Reconciliation failed for invoice %s", invoice_id)
            counts["failed"] += 1
    return counts
