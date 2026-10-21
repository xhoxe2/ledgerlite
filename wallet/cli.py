"""Command-line tools: python -m wallet.cli create-account <name>."""

import argparse
import asyncio
from uuid import UUID, uuid4

from wallet.auth import hash_api_key, new_api_key
from wallet.config import Settings
from wallet.db import create_pool


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m wallet.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-account", help="create an account, print its API key once")
    create.add_argument("name")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "create-account":
        account_id, api_key = asyncio.run(create_account(settings.database_url, args.name))
        print(f"account_id: {account_id}")
        print(f"api_key: {api_key}  (shown once, store it now)")


if __name__ == "__main__":
    main()
