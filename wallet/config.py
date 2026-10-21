"""Settings read from the environment."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    provider_url: str
    provider_api_key: str
    webhook_secret: str
    callback_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ["DATABASE_URL"],
            provider_url=os.environ["PROVIDER_URL"],
            provider_api_key=os.environ["PROVIDER_API_KEY"],
            webhook_secret=os.environ["WEBHOOK_SECRET"],
            callback_url=os.environ["CALLBACK_URL"],
        )
