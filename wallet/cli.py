"""Command-line tools: create an account, reconcile pending deposits."""

import argparse
import asyncio
import logging
import sys
from uuid import UUID, uuid4

from wallet.auth import hash_api_key, new_api_key
from wallet.config import Settings
from wallet.db import create_pool
from wallet.deposits import reconcile
from wallet.provider import new_provider_client


async def create_account(database_url: str, name: str) -> tuple[UUID, str]:
    api_key = new_api_key()
    account_id = uuid4()
    pool = await create_pool(database_url)
    try:
        await pool.execute(
            "INSERT INTO accounts (id, name, api_key_hash) VALUES ($1, $2, $3)",
            account_id,
            name,
            hash_api_key(api_key),
        )
    finally:
        await pool.close()
    return account_id, api_key


async def run_reconcile(settings: Settings, older_than_minutes: int) -> dict[str, int]:
    pool = await create_pool(settings.database_url)
    try:
        async with new_provider_client(settings) as client:
            return await reconcile(pool, client, older_than_minutes)
    finally:
        await pool.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(prog="python -m wallet.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-account", help="create an account, print its API key once")
    create.add_argument("name")
    settle = commands.add_parser("reconcile", help="settle pending deposits whose webhook was lost")
    settle.add_argument("--older-than", type=int, default=15, metavar="MINUTES")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "create-account":
        account_id, api_key = asyncio.run(create_account(settings.database_url, args.name))
        print(f"account_id: {account_id}")
        print(f"api_key: {api_key}  (shown once, store it now)")
    elif args.command == "reconcile":
        counts = asyncio.run(run_reconcile(settings, args.older_than))
        print(" ".join(f"{name}={count}" for name, count in counts.items()))
        sys.exit(1 if counts["failed"] else 0)


if __name__ == "__main__":
    main()
