"""Settings read from the environment."""

import os
from dataclasses import dataclass


def _required(name: str) -> str:
    # A blank value is as bad as a missing one: a blank WEBHOOK_SECRET makes signatures forgeable.
    value = os.environ.get(name, "")
    if not value.strip():
        raise RuntimeError(f"Environment variable {name} must be set and not blank")
    return value


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
            database_url=_required("DATABASE_URL"),
            provider_url=_required("PROVIDER_URL"),
            provider_api_key=_required("PROVIDER_API_KEY"),
            webhook_secret=_required("WEBHOOK_SECRET"),
            callback_url=_required("CALLBACK_URL"),
        )
