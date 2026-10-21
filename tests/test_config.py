"""Settings.from_env: every required value must be present and not blank."""

import pytest

from wallet.config import Settings

ENVIRONMENT = {
    "DATABASE_URL": "postgresql://ledger:ledger@db:5432/ledger",
    "PROVIDER_URL": "http://provider:8001",
    "PROVIDER_API_KEY": "provider-key",
    "WEBHOOK_SECRET": "webhook-secret",
    "CALLBACK_URL": "http://wallet:8000/v1/webhooks/provider",
}


@pytest.fixture
def environment(monkeypatch):
    for name, value in ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_settings_are_read_from_the_environment(environment):
    assert Settings.from_env() == Settings(
        database_url="postgresql://ledger:ledger@db:5432/ledger",
        provider_url="http://provider:8001",
        provider_api_key="provider-key",
        webhook_secret="webhook-secret",
        callback_url="http://wallet:8000/v1/webhooks/provider",
    )


@pytest.mark.parametrize(
    ("name", "blank"),
    [(name, "") for name in ENVIRONMENT] + [("WEBHOOK_SECRET", "   ")],
    ids=[f"{name}-empty" for name in ENVIRONMENT] + ["WEBHOOK_SECRET-spaces"],
)
def test_a_blank_required_value_is_refused_and_named(environment, name, blank):
    environment.setenv(name, blank)
    with pytest.raises(RuntimeError, match=name):
        Settings.from_env()
