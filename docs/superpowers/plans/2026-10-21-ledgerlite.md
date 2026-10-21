# LedgerLite: план реалізації

> **Для агентів-виконавців:** ОБОВ'ЯЗКОВИЙ ПІД-СКІЛ: superpowers:subagent-driven-development (рекомендовано) або superpowers:executing-plans — задача за задачею. Кроки позначено чекбоксами (`- [ ]`).

**Мета:** два сервіси LedgerLite (wallet і provider-mock) за тестовим завданням, з тестами на PostgreSQL, Docker і CI.

**Архітектура:** обидва сервіси — FastAPI-застосунки з фабрикою `create_app()`. Wallet працює з PostgreSQL через asyncpg і SQL вручну. Гроші — `Decimal`, баланс змінює лише `ledger.post_entry()`, ідемпотентність і блокування тримаються на транзакціях Postgres. Мок зберігає інвойси в пам'яті й підписує вебхуки HMAC-SHA256.

**Стек:** Python 3.12, FastAPI, uvicorn, asyncpg, httpx; pytest із плагіном anyio, ruff; uv; PostgreSQL 17; Docker Compose; GitHub Actions.

**Специфікація:** `docs/superpowers/specs/2026-10-21-ledgerlite-design.md`. План спирається на неї, тож виконавець читає обидва документи.

## Глобальні обмеження

- Python 3.12 (`requires-python = ">=3.12"`). Залежності лише `fastapi`, `uvicorn`, `asyncpg`, `httpx`; для розробки — `pytest`, `ruff`. Нова залежність — тільки з дозволу людини.
- Суми в JSON — рядки, у коді — `Decimal`. Округлення завжди з явним `rounding=ROUND_HALF_UP`. Float для грошей заборонений.
- Відповіді wallet ідуть лише через Pydantic-моделі: зі звичайного `dict` FastAPI віддає `Decimal` як float (перевірено на FastAPI 0.156).
- Помилки wallet — лише через `ApiError`, у форматі `{"error": {"code", "message", "details"}}`, з кодами з розділу 7 специфікації.
- SQL — лише з параметрами `$1, $2, …`, жодного форматування рядків.
- TDD: спершу тест, побачити, що він падає з очікуваної причини, потім код.
- Тести працюють зі справжнім PostgreSQL за адресою з `TEST_DATABASE_URL` (за замовчуванням `postgresql://ledger:ledger@localhost:55432/ledger_test`).
- Коментарі в коді — англійською й лише про «чому». Відоме обмеження позначається так: `# shortcut: <межа>, <коли покращити>`.
- README і AI_NOTES — українською.
- Коміт: префікс `feat:`, `fix:`, `test:`, `chore:` або `docs:`; останній рядок `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Виправлення помилки AI — окремим комітом із рядком `AI_NOTES: <що було не так і як помітили>` у тілі.
- Перед кожним комітом `uv run ruff format . && uv run ruff check . && uv run pytest` мають пройти без помилок.

## Передумови

Потрібен PostgreSQL на `localhost:55432` з користувачем `ledger` і базою `ledger_test`. Підійде будь-який спосіб:

- після задачі 7 — `docker compose up -d db`;
- до неї — тимчасовий кластер із Homebrew, де `PGDATA_DIR` — будь-яка тимчасова папка:

```bash
PG=/opt/homebrew/opt/postgresql@17/bin
"$PG/initdb" -D "$PGDATA_DIR" -U ledger --auth=trust
"$PG/pg_ctl" -D "$PGDATA_DIR" -l "$PGDATA_DIR/server.log" \
  -o "-p 55432 -c listen_addresses=localhost -c unix_socket_directories=''" start
"$PG/createdb" -h localhost -p 55432 -U ledger ledger_test
```

`unix_socket_directories=''` потрібен тому, що на macOS шлях до сокета в довгій тимчасовій папці перевищує ліміт довжини.

## Структура файлів

| Файл | Відповідальність | Задача |
|---|---|---|
| `pyproject.toml`, `uv.lock`, `.python-version` | залежності, ruff, pytest | 1 |
| `wallet/money.py` | типи сум, комісія, ціна кредитів | 1 |
| `wallet/schema.sql` | схема БД | 2 |
| `wallet/config.py` | `Settings` з оточення | 2 |
| `wallet/db.py` | пул asyncpg і застосування схеми | 2 |
| `wallet/errors.py` | `ApiError` і обробники помилок | 2 |
| `wallet/auth.py` | ключі та `current_account_id` | 2 |
| `wallet/accounts.py` | `GET /v1/accounts/me` | 2 |
| `wallet/app.py` | `create_app()` | 2, далі доповнюється |
| `wallet/cli.py` | `create-account`, згодом `reconcile` | 2, 10 |
| `provider_mock/app.py` | увесь мок провайдера | 3 |
| `wallet/provider.py` | клієнт провайдера та перевірка підпису | 4, 5, 10 |
| `wallet/deposits.py` | депозити, вебхук, звірка | 4, 5, 10 |
| `wallet/ledger.py` | `post_entry()` | 5 |
| `wallet/idempotency.py` | ключі ідемпотентності | 6 |
| `wallet/transfers.py` | `POST /v1/transfers` | 6 |
| `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `docker/create-test-db.sql`, `.env.example`, `.github/workflows/ci.yml` | запуск і CI | 7 |
| `wallet/purchases.py` | `POST /v1/purchases` | 8 |
| `wallet/transactions.py` | `GET /v1/transactions` | 9 |
| `wallet/ratelimit.py` | `rate_limited_account_id` | 11 |
| `README.md`, `AI_NOTES.md` | документація | 12 |
| `tests/conftest.py` | спільні фікстури | 2, 4, 5 |
| `tests/test_*.py` | тести відповідних модулів | кожна задача |

---

### Задача 1: Каркас проєкту і правила грошей

**Файли:**
- Створити: `pyproject.toml` (через uv), `.python-version`, `uv.lock`, `wallet/__init__.py`, `wallet/money.py`, `tests/test_money.py`

**Інтерфейси:**
- Споживає: нічого.
- Надає: `wallet.money.to_cents(amount: Decimal) -> Decimal`, `parse_amount(value: object) -> Decimal`, `PositiveMoney`, `NonNegativeMoney` (анотовані типи `Decimal`), `provider_fee(paid: Decimal) -> Decimal`, `credits_price(credits: int) -> Decimal`.

- [ ] **Крок 1: Створити проєкт**

```bash
uv init --bare --name ledgerlite --python 3.12
uv python pin 3.12
uv add fastapi uvicorn asyncpg httpx
uv add --dev pytest ruff
mkdir -p wallet tests && touch wallet/__init__.py
```

Дописати в кінець `pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "S"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "S105", "S106"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Крок 2: Написати тест, що падає** — `tests/test_money.py`

```python
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from wallet.money import NonNegativeMoney, PositiveMoney, credits_price, provider_fee


class Payment(BaseModel):
    amount: PositiveMoney


class PaidAmount(BaseModel):
    amount: NonNegativeMoney


@pytest.mark.parametrize(
    ("paid", "fee", "credited"),
    [
        ("10.00", "0.15", "9.85"),
        ("0.99", "0.01", "0.98"),
        ("33.33", "0.50", "32.83"),
        ("1.00", "0.02", "0.98"),  # float arithmetic gives a 0.01 fee
        ("3.00", "0.05", "2.95"),  # ROUND_HALF_EVEN gives a 0.04 fee
    ],
)
def test_provider_fee_is_rounded_half_up(paid, fee, credited):
    assert provider_fee(Decimal(paid)) == Decimal(fee)
    assert Decimal(paid) - provider_fee(Decimal(paid)) == Decimal(credited)


@pytest.mark.parametrize(
    ("credits", "price"),
    [
        (333, "33.30"),
        (507, "48.17"),  # float and ROUND_HALF_EVEN give 48.16
        (1005, "87.94"),
        (499, "49.90"),
        (500, "47.50"),
        (999, "94.91"),  # ROUND_HALF_EVEN gives 94.90
        (1000, "87.50"),
    ],
)
def test_credits_price_applies_discount_tiers(credits, price):
    assert credits_price(credits) == Decimal(price)


@pytest.mark.parametrize("raw", ["10.00", "10", "10.5", "0.01", "999999999999.99"])
def test_amount_accepts_decimal_strings(raw):
    assert Payment.model_validate({"amount": raw}).amount == Decimal(raw)


def test_amount_is_normalized_to_cents():
    assert str(Payment.model_validate({"amount": "10"}).amount) == "10.00"


@pytest.mark.parametrize(
    "raw_json",
    [
        "10.5",  # a JSON number, not a string
        '"10.001"',
        '"1e2"',
        '" 5.00 "',
        '"-1.00"',
        '"01.00"',
        '"0"',
        '"0.00"',
        '"NaN"',
        '"\\u0663"',  # ARABIC-INDIC DIGIT THREE: Decimal() would accept it
        "null",
        '"1000000000000.00"',
    ],
)
def test_amount_rejects_anything_else(raw_json):
    with pytest.raises(ValidationError):
        Payment.model_validate_json(f'{{"amount": {raw_json}}}')


def test_non_negative_money_accepts_zero():
    assert PaidAmount.model_validate({"amount": "0.00"}).amount == Decimal("0.00")
```

- [ ] **Крок 3: Переконатися, що тест падає**

Команда: `uv run pytest tests/test_money.py -v`
Очікувано: збій збирання — `ModuleNotFoundError: No module named 'wallet.money'`.

- [ ] **Крок 4: Мінімальна реалізація** — `wallet/money.py`

```python
"""Money rules: amounts from JSON, the provider fee and the price of credits."""

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import BeforeValidator, Field

CENT = Decimal("0.01")
PROVIDER_FEE_RATE = Decimal("0.015")
CREDIT_BASE_PRICE = Decimal("0.10")

# [0-9] rather than \d: \d also matches non-ASCII digits, and Decimal() accepts those.
_AMOUNT = re.compile(r"(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?")


def to_cents(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_amount(value: object) -> Decimal:
    if not isinstance(value, str) or not _AMOUNT.fullmatch(value):
        raise ValueError("amount must be a string such as '10.00' with at most 2 decimal places")
    return to_cents(Decimal(value))


PositiveMoney = Annotated[Decimal, BeforeValidator(parse_amount), Field(gt=0)]
NonNegativeMoney = Annotated[Decimal, BeforeValidator(parse_amount), Field(ge=0)]


def provider_fee(paid: Decimal) -> Decimal:
    return to_cents(paid * PROVIDER_FEE_RATE)


def credits_price(credits: int) -> Decimal:
    if credits >= 1000:
        price_factor = Decimal("0.875")
    elif credits >= 500:
        price_factor = Decimal("0.95")
    else:
        price_factor = Decimal("1")
    return to_cents(credits * CREDIT_BASE_PRICE * price_factor)
```

- [ ] **Крок 5: Переконатися, що тести проходять**

Команда: `uv run pytest tests/test_money.py -v`
Очікувано: `31 passed`.

- [ ] **Крок 6: Лінтер і коміт**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest
git add pyproject.toml uv.lock .python-version wallet/__init__.py wallet/money.py tests/test_money.py
git commit -m "feat: money rules for amounts, provider fee and credits price" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Задача 2: Каркас wallet — помилки, БД, ключі, `GET /v1/accounts/me`

**Файли:**
- Створити: `wallet/schema.sql`, `wallet/config.py`, `wallet/db.py`, `wallet/errors.py`, `wallet/auth.py`, `wallet/accounts.py`, `wallet/app.py`, `wallet/cli.py`, `tests/conftest.py`, `tests/test_errors.py`, `tests/test_accounts.py`

**Інтерфейси:**
- Споживає: нічого з попередніх задач.
- Надає:
  - `wallet.config.Settings(database_url, provider_url, provider_api_key, webhook_secret, callback_url)` і `Settings.from_env()`;
  - `wallet.db.create_pool(database_url: str) -> asyncpg.Pool`, `SCHEMA_SQL: str`;
  - `wallet.errors.ApiError(status_code: int, code: str, message: str, details: dict | None = None, headers: dict[str, str] | None = None)` з методом `.body() -> dict`, а також `error_body(code, message, details=None) -> dict` і `install_error_handlers(app)`;
  - `wallet.auth.new_api_key() -> str`, `hash_api_key(api_key: str) -> str`, залежність `current_account_id(...) -> UUID`;
  - `wallet.app.create_app(settings: Settings | None = None) -> FastAPI` зі станом `app.state.pool` і `app.state.settings`;
  - `wallet.cli.create_account(database_url: str, name: str) -> tuple[UUID, str]`;
  - фікстури `tests/conftest.py`: `anyio_backend`, `settings`, `app`, `client`, `make_account(name="alice", balance_usd="0.00") -> (UUID, headers)`; константа `WEBHOOK_SECRET`.

- [ ] **Крок 1: Схема** — `wallet/schema.sql`

```sql
CREATE TABLE IF NOT EXISTS accounts (
    id              UUID PRIMARY KEY,
    name            TEXT NOT NULL,
    api_key_hash    TEXT NOT NULL UNIQUE,
    balance_usd     NUMERIC(14, 2) NOT NULL DEFAULT 0 CHECK (balance_usd >= 0),
    balance_credits BIGINT NOT NULL DEFAULT 0 CHECK (balance_credits >= 0),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS deposits (
    id                 UUID PRIMARY KEY,
    account_id         UUID NOT NULL REFERENCES accounts (id),
    amount             NUMERIC(14, 2) NOT NULL CHECK (amount > 0),
    status             TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'partially_paid', 'expired')),
    invoice_id         TEXT NOT NULL UNIQUE,
    pay_url            TEXT NOT NULL,
    paid_amount        NUMERIC(14, 2),
    fee                NUMERIC(14, 2),
    credited           NUMERIC(14, 2),
    closed_by_event_id TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS deposits_pending_created_at ON deposits (created_at) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS ledger_entries (
    id              BIGSERIAL PRIMARY KEY,
    account_id      UUID NOT NULL REFERENCES accounts (id),
    type            TEXT NOT NULL CHECK (type IN ('deposit', 'transfer_in', 'transfer_out', 'purchase')),
    amount_usd      NUMERIC(14, 2) NOT NULL,
    credits         BIGINT NOT NULL DEFAULT 0,
    ref_id          UUID NOT NULL,
    counterparty_id UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ledger_entries_account_id ON ledger_entries (account_id, id DESC);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    account_id   UUID NOT NULL REFERENCES accounts (id),
    key          TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status_code  INT,
    response     TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, key)
);

CREATE TABLE IF NOT EXISTS rate_limits (
    account_id   UUID NOT NULL REFERENCES accounts (id),
    window_start TIMESTAMPTZ NOT NULL,
    count        INT NOT NULL,
    PRIMARY KEY (account_id, window_start)
);
```

- [ ] **Крок 2: Фікстури** — `tests/conftest.py`

```python
"""Shared fixtures: a wallet app on the test database and a way to create accounts."""

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from wallet.app import create_app
from wallet.auth import hash_api_key, new_api_key
from wallet.config import Settings

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://ledger:ledger@localhost:55432/ledger_test"
)
WEBHOOK_SECRET = "test-webhook-secret"

MakeAccount = Callable[..., Awaitable[tuple[UUID, dict[str, str]]]]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        provider_url="http://provider.test",
        provider_api_key="test-provider-key",
        webhook_secret=WEBHOOK_SECRET,
        callback_url="http://wallet.test/v1/webhooks/provider",
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        await application.state.pool.execute(
            "TRUNCATE accounts, deposits, ledger_entries, idempotency_keys, rate_limits"
        )
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://wallet.test") as http:
        yield http


@pytest.fixture
def make_account(app: FastAPI) -> MakeAccount:
    async def create(
        name: str = "alice", balance_usd: str = "0.00"
    ) -> tuple[UUID, dict[str, str]]:
        """Inserts an account; returns its id and the headers that authenticate as it."""
        api_key = new_api_key()
        account_id = uuid4()
        await app.state.pool.execute(
            "INSERT INTO accounts (id, name, api_key_hash, balance_usd) VALUES ($1, $2, $3, $4)",
            account_id,
            name,
            hash_api_key(api_key),
            Decimal(balance_usd),
        )
        return account_id, {"X-API-Key": api_key}

    return create
```

- [ ] **Крок 3: Тести, що падають** — `tests/test_errors.py` і `tests/test_accounts.py`

```python
# tests/test_errors.py
import pytest

pytestmark = pytest.mark.anyio


async def test_unknown_route_uses_the_error_format(client):
    response = await client.get("/v1/unknown")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found", "details": {}}}


async def test_wrong_method_uses_the_error_format(client):
    response = await client.post("/v1/accounts/me")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
```

```python
# tests/test_accounts.py
import pytest

from wallet.auth import hash_api_key
from wallet.cli import create_account

pytestmark = pytest.mark.anyio


async def test_me_returns_balances_as_strings(client, make_account):
    account_id, headers = await make_account("alice", balance_usd="9.85")
    response = await client.get("/v1/accounts/me", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "account_id": str(account_id),
        "name": "alice",
        "balance_usd": "9.85",
        "balance_credits": 0,
    }


@pytest.mark.parametrize(
    "headers", [{}, {"X-API-Key": "ll_not-a-real-key"}], ids=["missing", "wrong"]
)
async def test_me_requires_a_valid_api_key(client, headers):
    response = await client.get("/v1/accounts/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_create_account_stores_only_a_hash_of_the_key(app, client, settings):
    account_id, api_key = await create_account(settings.database_url, "bob")
    stored = await app.state.pool.fetchval(
        "SELECT api_key_hash FROM accounts WHERE id = $1", account_id
    )
    assert stored == hash_api_key(api_key)
    assert api_key not in stored
    response = await client.get("/v1/accounts/me", headers={"X-API-Key": api_key})
    assert response.json()["name"] == "bob"
```

- [ ] **Крок 4: Переконатися, що тести падають**

Команда: `uv run pytest tests/test_errors.py tests/test_accounts.py -v`
Очікувано: збій збирання — `ModuleNotFoundError: No module named 'wallet.app'`.

- [ ] **Крок 5: Реалізація**

`wallet/config.py`:

```python
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
```

`wallet/db.py`:

```python
"""Connection pool and schema for the wallet database."""

from pathlib import Path

import asyncpg

SCHEMA_SQL = Path(__file__).with_name("schema.sql").read_text()


async def create_pool(database_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(database_url)
    # shortcut: the schema is applied on every start and is safe with one process only;
    # move to migrations (Alembic) once the schema starts to change.
    await pool.execute(SCHEMA_SQL)
    return pool
```

`wallet/errors.py`:

```python
"""ApiError and the one error format shared by every wallet endpoint."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

_ROUTING_CODES = {404: "not_found", 405: "method_not_allowed"}


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers

    def body(self) -> dict:
        return error_body(self.code, self.message, self.details)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def on_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Only loc/msg/type: "input" may echo secrets and "ctx" may hold objects JSON cannot encode.
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        if any(e["type"] == "json_invalid" for e in errors):
            body = error_body("invalid_json", "Request body is not valid JSON")
            return JSONResponse(body, status_code=400)
        body = error_body("validation_error", "Request validation failed", {"errors": errors})
        return JSONResponse(body, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _ROUTING_CODES.get(exc.status_code, "http_error")
        return JSONResponse(
            error_body(code, str(exc.detail)), status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(Exception)
    async def on_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(error_body("internal_error", "Internal server error"), status_code=500)
```

`wallet/auth.py`:

```python
"""API keys: generation, hashing and the dependency that authenticates a request."""

import hashlib
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import Header, Request

from wallet.errors import ApiError


def new_api_key() -> str:
    return f"ll_{secrets.token_urlsafe(32)}"


def hash_api_key(api_key: str) -> str:
    # A random 256-bit key needs no slow hash, and sha256 lets us find the account by it.
    return hashlib.sha256(api_key.encode()).hexdigest()


async def current_account_id(
    request: Request, x_api_key: Annotated[str | None, Header()] = None
) -> UUID:
    if x_api_key:
        account_id = await request.app.state.pool.fetchval(
            "SELECT id FROM accounts WHERE api_key_hash = $1", hash_api_key(x_api_key)
        )
        if account_id is not None:
            return account_id
    raise ApiError(401, "unauthorized", "Missing or invalid X-API-Key")
```

`wallet/accounts.py`:

```python
"""GET /v1/accounts/me: the caller's balances."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from wallet.auth import current_account_id

router = APIRouter()


class AccountOut(BaseModel):
    account_id: UUID
    name: str
    balance_usd: Decimal
    balance_credits: int


@router.get("/v1/accounts/me", response_model=AccountOut)
async def get_my_account(
    request: Request, account_id: Annotated[UUID, Depends(current_account_id)]
) -> AccountOut:
    row = await request.app.state.pool.fetchrow(
        "SELECT name, balance_usd, balance_credits FROM accounts WHERE id = $1", account_id
    )
    return AccountOut(account_id=account_id, **dict(row))
```

`wallet/app.py`:

```python
"""Wallet service application factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from wallet import accounts
from wallet.config import Settings
from wallet.db import create_pool
from wallet.errors import install_error_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pool = await create_pool(settings.database_url)
        try:
            yield
        finally:
            await app.state.pool.close()

    app = FastAPI(title="LedgerLite wallet", lifespan=lifespan)
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(accounts.router)
    return app
```

`wallet/cli.py`:

```python
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
```

- [ ] **Крок 6: Переконатися, що тести проходять**

Команда: `uv run pytest -v`
Очікувано: `37 passed` (31 із задачі 1 і 6 нових).

- [ ] **Крок 7: Лінтер і коміт**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest
git add wallet tests
git commit -m "feat: wallet skeleton with error format, API keys and GET /v1/accounts/me" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Задача 3: provider-mock

**Файли:**
- Створити: `provider_mock/__init__.py`, `provider_mock/app.py`, `tests/test_provider_mock.py`

**Інтерфейси:**
- Споживає: нічого.
- Надає: `provider_mock.app.create_app(api_key=None, webhook_secret=None, public_url=None, webhook_transport=None) -> FastAPI` (без аргументів читає `PROVIDER_API_KEY`, `WEBHOOK_SECRET`, `PUBLIC_URL`), `sign(secret: str, timestamp: str, body: bytes) -> str`.

- [ ] **Крок 1: Тест, що падає** — `tests/test_provider_mock.py`

```python
"""Provider mock: invoices, final statuses and signed webhooks."""

import hashlib
import hmac
import json

import httpx
import pytest

from provider_mock.app import create_app

pytestmark = pytest.mark.anyio

API_KEY = "mock-key"
SECRET = "mock-secret"
AUTH = {"Authorization": f"Bearer {API_KEY}"}


def wallet_that_accepts(received: list[httpx.Request]) -> httpx.MockTransport:
    def receive(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200, json={"result": "applied"})

    return httpx.MockTransport(receive)


def mock_client(webhook_transport: httpx.MockTransport) -> httpx.AsyncClient:
    app = create_app(
        api_key=API_KEY,
        webhook_secret=SECRET,
        public_url="http://provider.test",
        webhook_transport=webhook_transport,
    )
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://provider.test")


@pytest.fixture
def webhooks() -> list[httpx.Request]:
    return []


@pytest.fixture
async def mock(webhooks):
    async with mock_client(wallet_that_accepts(webhooks)) as http:
        yield http


async def new_invoice(mock: httpx.AsyncClient, amount: str = "10.00") -> str:
    response = await mock.post(
        "/invoices",
        headers=AUTH,
        json={
            "amount": amount,
            "currency": "USD",
            "external_id": "dep-1",
            "callback_url": "http://wallet.test/hook",
        },
    )
    assert response.status_code == 201
    assert response.json()["pay_url"].startswith("http://provider.test/pay/inv_")
    return response.json()["invoice_id"]


async def simulate(mock: httpx.AsyncClient, invoice_id: str, **body) -> httpx.Response:
    return await mock.post(f"/invoices/{invoice_id}/simulate", headers=AUTH, json=body)


async def test_requests_need_the_bearer_key(mock):
    response = await mock.get("/invoices/inv_any", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


async def test_new_invoice_is_pending(mock):
    invoice_id = await new_invoice(mock)
    invoice = (await mock.get(f"/invoices/{invoice_id}", headers=AUTH)).json()
    assert (invoice["status"], invoice["paid_amount"]) == ("pending", "0.00")


async def test_paid_sends_one_signed_webhook(mock, webhooks):
    invoice_id = await new_invoice(mock)
    response = await simulate(mock, invoice_id, status="paid")
    assert response.json()["deliveries"] == [{"status_code": 200}]
    [hook] = webhooks
    timestamp = hook.headers["X-Timestamp"]
    digest = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + hook.content, hashlib.sha256)
    assert hook.headers["X-Signature"] == f"sha256={digest.hexdigest()}"
    event = json.loads(hook.content)
    assert event["event_id"].startswith("evt_")
    assert (event["invoice_id"], event["status"], event["paid_amount"]) == (
        invoice_id,
        "paid",
        "10.00",
    )


async def test_duplicate_sends_the_same_event_twice(mock, webhooks):
    invoice_id = await new_invoice(mock)
    await simulate(mock, invoice_id, status="partially_paid", paid_amount="7.50", duplicate=True)
    assert len(webhooks) == 2
    assert webhooks[0].content == webhooks[1].content


async def test_final_invoice_does_not_change(mock):
    invoice_id = await new_invoice(mock)
    await simulate(mock, invoice_id, status="expired", deliver_webhook=False)
    assert (await simulate(mock, invoice_id, status="paid")).status_code == 409
    invoice = (await mock.get(f"/invoices/{invoice_id}", headers=AUTH)).json()
    assert invoice["status"] == "expired"


async def test_partially_paid_needs_paid_amount(mock):
    invoice_id = await new_invoice(mock)
    assert (await simulate(mock, invoice_id, status="partially_paid")).status_code == 422


async def test_unreachable_wallet_is_reported_not_raised():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with mock_client(httpx.MockTransport(refuse)) as mock:
        invoice_id = await new_invoice(mock)
        response = await simulate(mock, invoice_id, status="paid")
    assert response.status_code == 200
    assert response.json()["deliveries"] == [{"error": "ConnectError"}]
```

- [ ] **Крок 2: Переконатися, що тест падає**

Команда: `uv run pytest tests/test_provider_mock.py -v`
Очікувано: збій збирання — `ModuleNotFoundError: No module named 'provider_mock'`.

- [ ] **Крок 3: Реалізація** — `provider_mock/__init__.py` (порожній) і `provider_mock/app.py`

```python
"""Payment provider mock: in-memory invoices, a simulate endpoint and signed webhooks."""

import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import UTC, datetime
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

Amount = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?$")]
FINAL_STATUSES = {"paid", "partially_paid", "expired"}
WEBHOOK_TIMEOUT = httpx.Timeout(5.0, connect=2.0)


class InvoiceIn(BaseModel):
    amount: Amount
    currency: Literal["USD"]
    external_id: str
    callback_url: str


class SimulateIn(BaseModel):
    status: Literal["paid", "partially_paid", "expired"]
    paid_amount: Amount | None = None
    deliver_webhook: bool = True
    duplicate: bool = False


def sign(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"sha256={digest.hexdigest()}"


def create_app(
    api_key: str | None = None,
    webhook_secret: str | None = None,
    public_url: str | None = None,
    webhook_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    api_key = api_key or os.environ["PROVIDER_API_KEY"]
    webhook_secret = webhook_secret or os.environ["WEBHOOK_SECRET"]
    public_url = public_url or os.environ.get("PUBLIC_URL", "http://localhost:8001")
    invoices: dict[str, dict] = {}

    def require_api_key(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {api_key}".encode()
        if not authorization or not hmac.compare_digest(authorization.encode(), expected):
            raise HTTPException(401, "Invalid API key")

    def find_invoice(invoice_id: str) -> dict:
        if invoice_id not in invoices:
            raise HTTPException(404, "Invoice not found")
        return invoices[invoice_id]

    async def deliver_webhook(invoice: dict, copies: int) -> list[dict]:
        event = {
            "event_id": f"evt_{secrets.token_hex(8)}",
            "invoice_id": invoice["invoice_id"],
            "status": invoice["status"],
            "paid_amount": invoice["paid_amount"],
            "occurred_at": datetime.now(UTC).isoformat(),
        }
        body = json.dumps(event).encode()
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "X-Timestamp": timestamp,
            "X-Signature": sign(webhook_secret, timestamp, body),
        }
        deliveries = []
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT, transport=webhook_transport) as client:
            for _ in range(copies):
                try:
                    response = await client.post(invoice["callback_url"], content=body, headers=headers)
                    deliveries.append({"status_code": response.status_code})
                except httpx.HTTPError as exc:
                    deliveries.append({"error": type(exc).__name__})
        return deliveries

    app = FastAPI(title="Payment provider mock", dependencies=[Depends(require_api_key)])

    @app.post("/invoices", status_code=201)
    async def create_invoice(body: InvoiceIn) -> dict:
        invoice_id = f"inv_{secrets.token_hex(8)}"
        invoices[invoice_id] = {
            "invoice_id": invoice_id,
            "status": "pending",
            "amount": body.amount,
            "paid_amount": "0.00",
            "currency": body.currency,
            "external_id": body.external_id,
            "callback_url": body.callback_url,
        }
        return {
            "invoice_id": invoice_id,
            "status": "pending",
            "pay_url": f"{public_url}/pay/{invoice_id}",
        }

    @app.get("/invoices/{invoice_id}")
    async def get_invoice(invoice_id: str) -> dict:
        return find_invoice(invoice_id)

    @app.post("/invoices/{invoice_id}/simulate")
    async def simulate(invoice_id: str, body: SimulateIn) -> dict:
        invoice = find_invoice(invoice_id)
        if invoice["status"] in FINAL_STATUSES:
            raise HTTPException(409, "Invoice is already final")
        if body.status == "partially_paid" and body.paid_amount is None:
            raise HTTPException(422, "paid_amount is required for partially_paid")
        default_paid = invoice["amount"] if body.status == "paid" else "0.00"
        invoice.update(status=body.status, paid_amount=body.paid_amount or default_paid)
        deliveries = []
        if body.deliver_webhook:
            deliveries = await deliver_webhook(invoice, copies=2 if body.duplicate else 1)
        return {"invoice": invoice, "deliveries": deliveries}

    return app
```

- [ ] **Крок 4: Переконатися, що тести проходять**

Команда: `uv run pytest -v`
Очікувано: `44 passed`.

- [ ] **Крок 5: Лінтер і коміт**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest
git add provider_mock tests/test_provider_mock.py
git commit -m "feat: provider mock with invoices, simulate and signed webhooks" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Задача 4: Депозити — клієнт провайдера і `POST /v1/deposits`

**Файли:**
- Створити: `wallet/provider.py`, `wallet/deposits.py`, `tests/test_deposits.py`
- Змінити: `wallet/app.py` (весь файл), `tests/conftest.py` (фікстура `app`, нові `FakeProvider` і `provider`)

**Інтерфейси:**
- Споживає: `Settings`, `ApiError`, `current_account_id`, `PositiveMoney`.
- Надає:
  - `wallet.provider.PROVIDER_TIMEOUT`, `new_provider_client(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient`, `create_invoice(client, *, amount: Decimal, external_id: str, callback_url: str) -> dict` з ключами `invoice_id` і `pay_url`;
  - `wallet.app.create_app(settings=None, provider_transport=None) -> FastAPI` зі станом `app.state.provider` (httpx-клієнт);
  - фікстура `provider` (`FakeProvider` з полями `requests` і `respond`).

- [ ] **Крок 1: Оновити фікстури** — `tests/conftest.py`

Після `settings` додати клас і фікстуру, а фікстуру `app` замінити цією:

```python
class FakeProvider:
    """Stands in for the payment provider; a test sets `respond` to script its answers."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.respond: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(500)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
async def app(settings: Settings, provider: FakeProvider) -> AsyncIterator[FastAPI]:
    application = create_app(settings, provider_transport=httpx.MockTransport(provider.handle))
    async with application.router.lifespan_context(application):
        await application.state.pool.execute(
            "TRUNCATE accounts, deposits, ledger_entries, idempotency_keys, rate_limits"
        )
        yield application
```

- [ ] **Крок 2: Тест, що падає** — `tests/test_deposits.py`

```python
"""POST /v1/deposits: invoice creation at the provider and its failure modes."""

import json
from uuid import UUID

import httpx
import pytest

pytestmark = pytest.mark.anyio

INVOICE = {"invoice_id": "inv_1", "status": "pending", "pay_url": "http://provider.test/pay/inv_1"}


async def test_deposit_creates_an_invoice_and_stores_it_pending(app, client, make_account, provider):
    account_id, headers = await make_account()
    provider.respond = lambda request: httpx.Response(201, json=INVOICE)
    response = await client.post("/v1/deposits", headers=headers, json={"amount": "10.00"})
    assert response.status_code == 201
    body = response.json()
    assert (body["status"], body["amount"], body["pay_url"]) == (
        "pending",
        "10.00",
        INVOICE["pay_url"],
    )
    [sent] = provider.requests
    assert sent.headers["Authorization"] == "Bearer test-provider-key"
    assert json.loads(sent.content) == {
        "amount": "10.00",
        "currency": "USD",
        "external_id": body["deposit_id"],
        "callback_url": "http://wallet.test/v1/webhooks/provider",
    }
    row = await app.state.pool.fetchrow(
        "SELECT account_id, status, invoice_id FROM deposits WHERE id = $1", UUID(body["deposit_id"])
    )
    assert dict(row) == {"account_id": account_id, "status": "pending", "invoice_id": "inv_1"}


def failing_with(error_class):
    def fail(request: httpx.Request) -> httpx.Response:
        raise error_class("provider is down", request=request)

    return fail


@pytest.mark.parametrize(
    ("respond", "status", "code"),
    [
        (failing_with(httpx.ReadTimeout), 504, "provider_timeout"),
        (failing_with(httpx.ConnectError), 503, "provider_unavailable"),
        (lambda request: httpx.Response(500, json={"detail": "boom"}), 502, "provider_error"),
        (lambda request: httpx.Response(201, json={"status": "pending"}), 502, "provider_error"),
    ],
    ids=["timeout", "unreachable", "provider-5xx", "missing-fields"],
)
async def test_provider_failure_is_a_clear_error_and_stores_nothing(
    app, client, make_account, provider, respond, status, code
):
    _, headers = await make_account()
    provider.respond = respond
    response = await client.post("/v1/deposits", headers=headers, json={"amount": "10.00"})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert await app.state.pool.fetchval("SELECT count(*) FROM deposits") == 0


async def test_amount_as_a_number_is_422(client, make_account):
    _, headers = await make_account()
    response = await client.post("/v1/deposits", headers=headers, json={"amount": 10.5})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_malformed_json_is_400(client, make_account):
    _, headers = await make_account()
    response = await client.post(
        "/v1/deposits", headers={**headers, "Content-Type": "application/json"}, content=b"{oops"
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_json"
```

- [ ] **Крок 3: Переконатися, що тести падають**

Команда: `uv run pytest -v`
Очікувано: збій збирання — `TypeError: create_app() got an unexpected keyword argument 'provider_transport'`, тобто падають усі тести, що беруть фікстуру `app`.

- [ ] **Крок 4: Реалізація**

`wallet/provider.py`:

```python
"""Payment provider contract: invoice calls out to the provider."""

from decimal import Decimal

import httpx

from wallet.config import Settings
from wallet.errors import ApiError

PROVIDER_TIMEOUT = httpx.Timeout(5.0, connect=2.0)


def new_provider_client(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.provider_url,
        headers={"Authorization": f"Bearer {settings.provider_api_key}"},
        timeout=PROVIDER_TIMEOUT,
        transport=transport,
    )


async def create_invoice(
    client: httpx.AsyncClient, *, amount: Decimal, external_id: str, callback_url: str
) -> dict:
    payload = {
        "amount": str(amount),
        "currency": "USD",
        "external_id": external_id,
        "callback_url": callback_url,
    }
    return await _call(client, "POST", "/invoices", json=payload, required=("invoice_id", "pay_url"))


async def _call(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    required: tuple[str, ...],
    json: dict | None = None,
) -> dict:
    try:
        response = await client.request(method, url, json=json)
    except httpx.TimeoutException as exc:  # a RequestError subclass, so it is caught first
        raise ApiError(504, "provider_timeout", "Payment provider did not respond in time") from exc
    except httpx.RequestError as exc:
        raise ApiError(503, "provider_unavailable", "Payment provider is unavailable") from exc
    if response.is_error:
        raise ApiError(
            502,
            "provider_error",
            "Payment provider returned an error",
            {"provider_status": response.status_code},
        )
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or not all(isinstance(payload.get(k), str) for k in required):
        raise ApiError(502, "provider_error", "Payment provider returned an unexpected response")
    return payload
```

`wallet/deposits.py`:

```python
"""Deposits: an invoice at the provider, closed later by the provider's webhook."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from wallet import provider
from wallet.auth import current_account_id
from wallet.money import PositiveMoney

router = APIRouter()


class DepositIn(BaseModel):
    amount: PositiveMoney


class DepositOut(BaseModel):
    deposit_id: UUID
    status: str
    amount: Decimal
    pay_url: str


@router.post("/v1/deposits", status_code=201, response_model=DepositOut)
async def create_deposit(
    request: Request,
    body: DepositIn,
    account_id: Annotated[UUID, Depends(current_account_id)],
) -> DepositOut:
    deposit_id = uuid4()
    # Called outside any DB transaction; the deposit is stored only once the invoice exists,
    # so a provider failure leaves nothing behind.
    invoice = await provider.create_invoice(
        request.app.state.provider,
        amount=body.amount,
        external_id=str(deposit_id),
        callback_url=request.app.state.settings.callback_url,
    )
    await request.app.state.pool.execute(
        """
        INSERT INTO deposits (id, account_id, amount, status, invoice_id, pay_url)
        VALUES ($1, $2, $3, 'pending', $4, $5)
        """,
        deposit_id,
        account_id,
        body.amount,
        invoice["invoice_id"],
        invoice["pay_url"],
    )
    return DepositOut(
        deposit_id=deposit_id, status="pending", amount=body.amount, pay_url=invoice["pay_url"]
    )
```

`wallet/app.py` (весь файл):

```python
"""Wallet service application factory."""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from wallet import accounts, deposits
from wallet.config import Settings
from wallet.db import create_pool
from wallet.errors import install_error_handlers
from wallet.provider import new_provider_client


def create_app(
    settings: Settings | None = None,
    provider_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pool = await create_pool(settings.database_url)
        app.state.provider = new_provider_client(settings, provider_transport)
        try:
            yield
        finally:
            await app.state.provider.aclose()
            await app.state.pool.close()

    app = FastAPI(title="LedgerLite wallet", lifespan=lifespan)
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(accounts.router)
    app.include_router(deposits.router)
    return app
```

- [ ] **Крок 5: Переконатися, що тести проходять**

Команда: `uv run pytest -v`
Очікувано: `51 passed`.

- [ ] **Крок 6: Лінтер і коміт**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest
git add wallet tests
git commit -m "feat: POST /v1/deposits with provider timeouts and clear failures" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Задача 5: Вебхук провайдера

**Файли:**
- Створити: `wallet/ledger.py`, `tests/test_webhooks.py`
- Змінити: `wallet/provider.py` (додати `verify_signature`), `wallet/deposits.py` (весь файл), `tests/conftest.py` (фікстура `signed_webhook`)

**Інтерфейси:**
- Споживає: `ApiError`, `provider_fee`, `NonNegativeMoney`, `PositiveMoney`, `current_account_id`, `provider.create_invoice`.
- Надає:
  - `wallet.provider.verify_signature(secret: str, raw_body: bytes, *, timestamp: str | None, signature: str | None) -> None` (кидає `ApiError` 401);
  - `wallet.ledger.post_entry(conn, *, account_id: UUID, entry_type: str, amount_usd: Decimal, ref_id: UUID, credits: int = 0, counterparty_id: UUID | None = None) -> asyncpg.Record` з полями `balance_usd` і `balance_credits` (кидає `ApiError` 409 `insufficient_funds`);
  - `wallet.deposits.InvoiceStatus` (Literal), `FINAL_STATUSES`, `apply_invoice_status(conn, *, invoice_id: str, status: InvoiceStatus, paid_amount: Decimal, event_id: str | None) -> Literal["applied", "ignored"]`;
  - фікстура `signed_webhook(payload: dict | bytes, *, secret=WEBHOOK_SECRET, timestamp_offset=0) -> (body, headers)`.

- [ ] **Крок 1: Фікстура підпису** — `tests/conftest.py`

Додати імпорти `hashlib`, `hmac`, `json`, `time` і фікстуру:

```python
@pytest.fixture
def signed_webhook() -> Callable[..., tuple[bytes, dict[str, str]]]:
    def build(
        payload: dict | bytes, *, secret: str = WEBHOOK_SECRET, timestamp_offset: int = 0
    ) -> tuple[bytes, dict[str, str]]:
        """Signs a webhook body the way the provider contract describes."""
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        timestamp = str(int(time.time()) + timestamp_offset)
        digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
        headers = {
            "Content-Type": "application/json",
            "X-Timestamp": timestamp,
            "X-Signature": f"sha256={digest.hexdigest()}",
        }
        return body, headers

    return build
```

- [ ] **Крок 2: Тест, що падає** — `tests/test_webhooks.py`

```python
"""Provider webhooks: signature checks, crediting, duplicates and late events."""

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.anyio

EVENT = {
    "event_id": "evt_1",
    "invoice_id": "inv_1",
    "status": "paid",
    "paid_amount": "10.00",
    "occurred_at": "2026-10-21T12:00:00+00:00",
}


@pytest.fixture
def pending_deposit(app, make_account):
    async def create(amount: str = "10.00", invoice_id: str = "inv_1") -> UUID:
        account_id, _ = await make_account()
        await app.state.pool.execute(
            """
            INSERT INTO deposits (id, account_id, amount, status, invoice_id, pay_url)
            VALUES ($1, $2, $3, 'pending', $4, 'http://provider.test/pay')
            """,
            uuid4(),
            account_id,
            Decimal(amount),
            invoice_id,
        )
        return account_id

    return create


@pytest.fixture
def send_webhook(client, signed_webhook):
    async def send(secret=None, timestamp_offset=0, **event_fields):
        signing = {"timestamp_offset": timestamp_offset} | ({"secret": secret} if secret else {})
        body, headers = signed_webhook(EVENT | event_fields, **signing)
        return await client.post("/v1/webhooks/provider", content=body, headers=headers)

    return send


async def balance_of(app, account_id) -> Decimal:
    return await app.state.pool.fetchval(
        "SELECT balance_usd FROM accounts WHERE id = $1", account_id
    )


async def test_paid_webhook_credits_amount_minus_fee(app, pending_deposit, send_webhook):
    account_id = await pending_deposit()
    response = await send_webhook()
    assert (response.status_code, response.json()) == (200, {"result": "applied"})
    assert await balance_of(app, account_id) == Decimal("9.85")
    deposit = await app.state.pool.fetchrow(
        "SELECT status, paid_amount, fee, credited, closed_by_event_id FROM deposits"
    )
    assert dict(deposit) == {
        "status": "paid",
        "paid_amount": Decimal("10.00"),
        "fee": Decimal("0.15"),
        "credited": Decimal("9.85"),
        "closed_by_event_id": "evt_1",
    }


async def test_partially_paid_credits_what_was_paid_minus_fee(app, pending_deposit, send_webhook):
    account_id = await pending_deposit()
    await send_webhook(status="partially_paid", paid_amount="7.50")
    assert await balance_of(app, account_id) == Decimal("7.39")  # fee 0.1125 rounds to 0.11


async def test_expired_closes_the_deposit_and_later_events_change_nothing(
    app, pending_deposit, send_webhook
):
    account_id = await pending_deposit()
    expired = await send_webhook(status="expired", paid_amount="0.00")
    late = await send_webhook(status="paid", event_id="evt_2")
    assert (expired.json(), late.json()) == ({"result": "applied"}, {"result": "ignored"})
    assert await balance_of(app, account_id) == Decimal("0.00")
    assert await app.state.pool.fetchval("SELECT status FROM deposits") == "expired"


async def test_repeated_event_is_credited_once(app, pending_deposit, send_webhook):
    account_id = await pending_deposit()
    first, second = await send_webhook(), await send_webhook()
    assert (first.json(), second.json()) == ({"result": "applied"}, {"result": "ignored"})
    assert await balance_of(app, account_id) == Decimal("9.85")


async def test_concurrent_duplicates_are_credited_once(app, pending_deposit, send_webhook):
    account_id = await pending_deposit()
    responses = await asyncio.gather(*(send_webhook() for _ in range(5)))
    assert sorted(r.json()["result"] for r in responses) == ["applied"] + ["ignored"] * 4
    assert await balance_of(app, account_id) == Decimal("9.85")
    assert await app.state.pool.fetchval("SELECT count(*) FROM ledger_entries") == 1


@pytest.mark.parametrize(
    "signing",
    [{"secret": "wrong-secret"}, {"timestamp_offset": -310}, {"timestamp_offset": 310}],
    ids=["wrong-secret", "expired", "from-the-future"],
)
async def test_bad_signature_is_401_and_credits_nothing(app, pending_deposit, send_webhook, signing):
    account_id = await pending_deposit()
    response = await send_webhook(**signing)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"
    assert await balance_of(app, account_id) == Decimal("0.00")


async def test_timestamp_inside_the_window_is_accepted(pending_deposit, send_webhook):
    await pending_deposit()
    assert (await send_webhook(timestamp_offset=-290)).status_code == 200


async def test_missing_signature_headers_are_401(client, pending_deposit):
    await pending_deposit()
    response = await client.post("/v1/webhooks/provider", json=EVENT)
    assert response.status_code == 401


async def test_tampered_body_is_401(client, pending_deposit, signed_webhook):
    await pending_deposit()
    body, headers = signed_webhook(EVENT | {"paid_amount": "1.00"})
    tampered = body.replace(b'"1.00"', b'"1000.00"')
    response = await client.post("/v1/webhooks/provider", content=tampered, headers=headers)
    assert response.status_code == 401


async def test_unknown_invoice_is_404(send_webhook):
    response = await send_webhook(invoice_id="inv_missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_invoice"


async def test_signed_but_invalid_event_is_422(pending_deposit, send_webhook):
    await pending_deposit()
    response = await send_webhook(status="refunded")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_signed_but_malformed_json_is_400(client, signed_webhook):
    body, headers = signed_webhook(b"{not json")
    response = await client.post("/v1/webhooks/provider", content=body, headers=headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_json"
```

- [ ] **Крок 3: Переконатися, що тести падають**

Команда: `uv run pytest tests/test_webhooks.py -v`
Очікувано: 14 тестів зібрано, падають із 404 `not_found` (маршруту `/v1/webhooks/provider` ще немає).

- [ ] **Крок 4: Реалізація**

У `wallet/provider.py` додати імпорти `hashlib`, `hmac`, `time`, оновити docstring модуля на `"""Payment provider contract: invoice calls out, webhook signatures in."""` і дописати:

```python
SIGNATURE_TOLERANCE_SECONDS = 300


def verify_signature(
    secret: str, raw_body: bytes, *, timestamp: str | None, signature: str | None
) -> None:
    """Raises 401 unless the body carries our signature and a timestamp within 5 minutes of now."""
    if not timestamp or not signature or not signature.startswith("sha256="):
        raise _invalid_signature()
    try:
        sent_at = int(timestamp)
    except ValueError:
        raise _invalid_signature() from None
    if abs(time.time() - sent_at) > SIGNATURE_TOLERANCE_SECONDS:
        raise _invalid_signature()
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256)
    # Bytes, not str: compare_digest raises TypeError on str with non-ASCII characters.
    if not hmac.compare_digest(
        expected.hexdigest().encode(), signature.removeprefix("sha256=").encode()
    ):
        raise _invalid_signature()


def _invalid_signature() -> ApiError:
    return ApiError(401, "invalid_signature", "Webhook signature is missing, stale or wrong")
```

`wallet/ledger.py`:

```python
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
```

`wallet/deposits.py` (весь файл):

```python
"""Deposits: an invoice at the provider, closed later by the provider's webhook."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, get_args
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from wallet import provider
from wallet.auth import current_account_id
from wallet.errors import ApiError
from wallet.ledger import post_entry
from wallet.money import NonNegativeMoney, PositiveMoney, provider_fee

router = APIRouter()

InvoiceStatus = Literal["paid", "partially_paid", "expired"]
FINAL_STATUSES = get_args(InvoiceStatus)


class DepositIn(BaseModel):
    amount: PositiveMoney


class DepositOut(BaseModel):
    deposit_id: UUID
    status: str
    amount: Decimal
    pay_url: str


class ProviderEvent(BaseModel):
    event_id: str
    invoice_id: str
    status: InvoiceStatus
    paid_amount: NonNegativeMoney
    occurred_at: datetime


class WebhookResult(BaseModel):
    result: Literal["applied", "ignored"]


@router.post("/v1/deposits", status_code=201, response_model=DepositOut)
async def create_deposit(
    request: Request,
    body: DepositIn,
    account_id: Annotated[UUID, Depends(current_account_id)],
) -> DepositOut:
    deposit_id = uuid4()
    # Called outside any DB transaction; the deposit is stored only once the invoice exists,
    # so a provider failure leaves nothing behind.
    invoice = await provider.create_invoice(
        request.app.state.provider,
        amount=body.amount,
        external_id=str(deposit_id),
        callback_url=request.app.state.settings.callback_url,
    )
    await request.app.state.pool.execute(
        """
        INSERT INTO deposits (id, account_id, amount, status, invoice_id, pay_url)
        VALUES ($1, $2, $3, 'pending', $4, $5)
        """,
        deposit_id,
        account_id,
        body.amount,
        invoice["invoice_id"],
        invoice["pay_url"],
    )
    return DepositOut(
        deposit_id=deposit_id, status="pending", amount=body.amount, pay_url=invoice["pay_url"]
    )


@router.post("/v1/webhooks/provider", response_model=WebhookResult)
async def provider_webhook(request: Request) -> WebhookResult:
    # The signature covers the raw bytes, so it is checked before the body is parsed.
    raw_body = await request.body()
    provider.verify_signature(
        request.app.state.settings.webhook_secret,
        raw_body,
        timestamp=request.headers.get("X-Timestamp"),
        signature=request.headers.get("X-Signature"),
    )
    try:
        event = ProviderEvent.model_validate_json(raw_body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    async with request.app.state.pool.acquire() as conn, conn.transaction():
        result = await apply_invoice_status(
            conn,
            invoice_id=event.invoice_id,
            status=event.status,
            paid_amount=event.paid_amount,
            event_id=event.event_id,
        )
    return WebhookResult(result=result)


async def apply_invoice_status(
    conn: asyncpg.Connection,
    *,
    invoice_id: str,
    status: InvoiceStatus,
    paid_amount: Decimal,
    event_id: str | None,
) -> Literal["applied", "ignored"]:
    """Closes a pending deposit exactly once; the webhook and reconciliation both use it."""
    deposit = await conn.fetchrow(
        "SELECT id, account_id, status FROM deposits WHERE invoice_id = $1 FOR UPDATE",
        invoice_id,
    )
    if deposit is None:
        raise ApiError(
            404, "unknown_invoice", "No deposit for this invoice", {"invoice_id": invoice_id}
        )
    if deposit["status"] != "pending":
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
```

- [ ] **Крок 5: Переконатися, що тести проходять**

Команда: `uv run pytest -v`
Очікувано: `65 passed`.

- [ ] **Крок 6: Лінтер і коміт**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest
git add wallet tests
git commit -m "feat: provider webhook with signature check and exactly-once crediting" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Задача 6: Перекази з ідемпотентністю

**Файли:**
- Створити: `wallet/idempotency.py`, `wallet/transfers.py`, `tests/test_transfers.py`
- Змінити: `wallet/app.py` — імпорт `from wallet import accounts, deposits, transfers` і рядок `app.include_router(transfers.router)` після `deposits`

**Інтерфейси:**
- Споживає: `post_entry`, `ApiError`, `current_account_id`, `PositiveMoney`.
- Надає: `wallet.idempotency.StoredResponse(status_code: int, body: str)`, `hash_request(payload: dict) -> str`, `claim(conn, account_id, key, request_hash) -> StoredResponse | None`, `save(conn, account_id, key, status_code, content: dict) -> StoredResponse`.

- [ ] **Крок 1: Тест, що падає** — `tests/test_transfers.py`

```python
"""Transfers: idempotency, insufficient funds and concurrency."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


async def transfer(client, headers, to_account_id, amount, key="key-1"):
    return await client.post(
        "/v1/transfers",
        headers={**headers, "Idempotency-Key": key},
        json={"to_account_id": str(to_account_id), "amount": amount},
    )


async def balance_of(app, account_id) -> Decimal:
    return await app.state.pool.fetchval(
        "SELECT balance_usd FROM accounts WHERE id = $1", account_id
    )


async def test_transfer_moves_money_and_records_both_legs(app, client, make_account):
    alice, headers = await make_account("alice", balance_usd="10.00")
    bob, _ = await make_account("bob")
    response = await transfer(client, headers, bob, "4.00")
    assert response.status_code == 201
    body = response.json()
    assert (body["from_account_id"], body["to_account_id"], body["amount"]) == (
        str(alice),
        str(bob),
        "4.00",
    )
    assert await balance_of(app, alice) == Decimal("6.00")
    assert await balance_of(app, bob) == Decimal("4.00")
    legs = await app.state.pool.fetch(
        "SELECT type, amount_usd, ref_id FROM ledger_entries ORDER BY id"
    )
    assert [(leg["type"], leg["amount_usd"]) for leg in legs] == [
        ("transfer_out", Decimal("-4.00")),
        ("transfer_in", Decimal("4.00")),
    ]
    assert {str(leg["ref_id"]) for leg in legs} == {body["transfer_id"]}


async def test_same_key_and_body_replays_the_exact_response(app, client, make_account):
    alice, headers = await make_account("alice", balance_usd="10.00")
    bob, _ = await make_account("bob")
    first = await transfer(client, headers, bob, "4.00")
    second = await transfer(client, headers, bob, "4.0")  # the same amount, written differently
    assert (second.status_code, second.content) == (first.status_code, first.content)
    assert await balance_of(app, alice) == Decimal("6.00")


async def test_same_key_with_another_body_is_409(app, client, make_account):
    alice, headers = await make_account("alice", balance_usd="10.00")
    bob, _ = await make_account("bob")
    await transfer(client, headers, bob, "4.00")
    response = await transfer(client, headers, bob, "5.00")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_key_reused"
    assert await balance_of(app, alice) == Decimal("6.00")


async def test_keys_are_scoped_per_account(client, make_account):
    _, alice_headers = await make_account("alice", balance_usd="10.00")
    _, bob_headers = await make_account("bob", balance_usd="10.00")
    carol, _ = await make_account("carol")
    await transfer(client, alice_headers, carol, "1.00", key="shared")
    response = await transfer(client, bob_headers, carol, "2.00", key="shared")
    assert response.status_code == 201


async def test_missing_idempotency_key_is_400(client, make_account):
    _, headers = await make_account("alice", balance_usd="10.00")
    bob, _ = await make_account("bob")
    response = await client.post(
        "/v1/transfers", headers=headers, json={"to_account_id": str(bob), "amount": "1.00"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_idempotency_key"


async def test_insufficient_funds_is_409_and_replays_the_same_error(app, client, make_account):
    alice, headers = await make_account("alice", balance_usd="3.00")
    bob, _ = await make_account("bob")
    first = await transfer(client, headers, bob, "5.00")
    assert first.status_code == 409
    assert first.json()["error"]["code"] == "insufficient_funds"
    await app.state.pool.execute("UPDATE accounts SET balance_usd = 100 WHERE id = $1", alice)
    replay = await transfer(client, headers, bob, "5.00")
    assert (replay.status_code, replay.content) == (first.status_code, first.content)
    assert await balance_of(app, bob) == Decimal("0.00")


async def test_unknown_recipient_is_404(client, make_account):
    _, headers = await make_account("alice", balance_usd="10.00")
    response = await transfer(client, headers, uuid4(), "1.00")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "account_not_found"


async def test_transfer_to_self_is_422(client, make_account):
    alice, headers = await make_account("alice", balance_usd="10.00")
    response = await transfer(client, headers, alice, "1.00")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "same_account"


@pytest.mark.parametrize("amount", ["0.00", "0.001"])
async def test_amount_below_one_cent_is_422(client, make_account, amount):
    _, headers = await make_account("alice", balance_usd="10.00")
    bob, _ = await make_account("bob")
    response = await transfer(client, headers, bob, amount)
    assert response.status_code == 422


async def test_parallel_transfers_never_overdraw(app, client, make_account):
    alice, headers = await make_account("alice", balance_usd="100.00")
    bob, _ = await make_account("bob")
    responses = await asyncio.gather(
        *(transfer(client, headers, bob, "10.00", key=f"k{i}") for i in range(20))
    )
    assert sorted(r.status_code for r in responses) == [201] * 10 + [409] * 10
    assert await balance_of(app, alice) == Decimal("0.00")
    assert await balance_of(app, bob) == Decimal("100.00")


async def test_parallel_retries_with_one_key_debit_once(app, client, make_account):
    alice, headers = await make_account("alice", balance_usd="10.00")
    bob, _ = await make_account("bob")
    responses = await asyncio.gather(
        *(transfer(client, headers, bob, "4.00", key="same") for _ in range(5))
    )
    assert {r.status_code for r in responses} == {201}
    assert len({r.content for r in responses}) == 1
    assert await balance_of(app, alice) == Decimal("6.00")


async def test_opposite_transfers_do_not_deadlock(app, client, make_account):
    alice, alice_headers = await make_account("alice", balance_usd="50.00")
    bob, bob_headers = await make_account("bob", balance_usd="50.00")
    calls = [transfer(client, alice_headers, bob, "1.00", key=f"a{i}") for i in range(10)]
    calls += [transfer(client, bob_headers, alice, "1.00", key=f"b{i}") for i in range(10)]
    responses = await asyncio.gather(*calls)
    assert {r.status_code for r in responses} == {201}
    assert await balance_of(app, alice) == Decimal("50.00")
    assert await balance_of(app, bob) == Decimal("50.00")
```

- [ ] **Крок 2: Переконатися, що тести падають**

Команда: `uv run pytest tests/test_transfers.py -v`
Очікувано: 13 тестів падають із 404 `not_found` (маршруту ще немає).

- [ ] **Крок 3: Реалізація**

`wallet/idempotency.py`:

```python
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
    # shortcut: keys are never deleted; add a TTL cleanup job when the table grows.
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
    # The INSERT above waited for any in-flight request with this key to commit,
    # so its saved response is visible to this statement.
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
```

`wallet/transfers.py`:

```python
"""POST /v1/transfers: moves USD between accounts, made idempotent by Idempotency-Key."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel

from wallet import idempotency
from wallet.auth import current_account_id
from wallet.errors import ApiError
from wallet.ledger import post_entry
from wallet.money import PositiveMoney

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
    account_id: Annotated[UUID, Depends(current_account_id)],
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
        stored = await idempotency.claim(conn, account_id, idempotency_key, request_hash)
        if stored is None:
            try:
                async with conn.transaction():  # savepoint: a business error leaves no writes
                    content = await _transfer(conn, account_id, body)
                status_code = 201
            except ApiError as exc:
                status_code, content = exc.status_code, exc.body()
            stored = await idempotency.save(conn, account_id, idempotency_key, status_code, content)
    return Response(stored.body, status_code=stored.status_code, media_type="application/json")


async def _transfer(conn: asyncpg.Connection, sender_id: UUID, body: TransferIn) -> dict:
    # Both rows are locked in id order, so opposite transfers (A->B, B->A) cannot deadlock.
    locked = await conn.fetch(
        "SELECT id FROM accounts WHERE id = ANY($1::uuid[]) ORDER BY id FOR UPDATE",
        [sender_id, body.to_account_id],
    )
    if len(locked) < 2:
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
```

У `wallet/app.py`: імпорт `from wallet import accounts, deposits, transfers` і рядок `app.include_router(transfers.router)` після `app.include_router(deposits.router)`.

- [ ] **Крок 4: Переконатися, що тести проходять**

Команда: `uv run pytest -v`
Очікувано: `78 passed`.

- [ ] **Крок 5: Лінтер і коміт**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest
git add wallet tests
git commit -m "feat: idempotent POST /v1/transfers that never overdraws" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Задача 7: Docker, compose і CI

**Файли:**
- Створити: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `docker/create-test-db.sql`, `.env.example`, `.github/workflows/ci.yml`

**Інтерфейси:**
- Споживає: `uvicorn --factory wallet.app:create_app`, `uvicorn --factory provider_mock.app:create_app`, `python -m wallet.cli`.
- Надає: `docker compose up` (wallet `:8000`, provider `:8001`, db `:55432`), CI на кожен push.

Тестів на pytest тут немає: перевірка — це реальний запуск compose і сценарій нижче.

- [ ] **Крок 1: Перевірити версії, які закріплюємо**

```bash
docker pull ghcr.io/astral-sh/uv:0.12.3
gh api repos/astral-sh/setup-uv/releases/latest --jq .tag_name
gh api repos/actions/checkout/releases/latest --jq .tag_name
```

Якщо тега `uv:0.12.3` немає, взяти версію з `uv --version`. У `ci.yml` поставити мажорні версії з відповідей `gh api` (у прикладі нижче — `v4` і `v6`).

- [ ] **Крок 2: Файли**

`Dockerfile`:

```dockerfile
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
COPY wallet ./wallet
COPY provider_mock ./provider_mock

RUN useradd --system app
USER app
EXPOSE 8000 8001
CMD ["uvicorn", "--factory", "wallet.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
```

`.dockerignore`:

```
.git
.venv
.env
__pycache__
.pytest_cache
.ruff_cache
```

`docker/create-test-db.sql`:

```sql
CREATE DATABASE ledger_test;
```

`docker-compose.yml`:

```yaml
services:
  db:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: ledger
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-ledger}
      POSTGRES_DB: ledger
    ports:
      - "55432:5432"
    volumes:
      - ./docker/create-test-db.sql:/docker-entrypoint-initdb.d/create-test-db.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ledger -d ledger"]
      interval: 2s
      timeout: 3s
      retries: 30

  provider:
    build: .
    command: ["uvicorn", "--factory", "provider_mock.app:create_app", "--host", "0.0.0.0", "--port", "8001"]
    environment:
      PROVIDER_API_KEY: ${PROVIDER_API_KEY:-dev-provider-key}
      WEBHOOK_SECRET: ${WEBHOOK_SECRET:-dev-webhook-secret}
      PUBLIC_URL: ${PUBLIC_URL:-http://localhost:8001}
    ports:
      - "8001:8001"

  wallet:
    build: .
    environment:
      DATABASE_URL: postgresql://ledger:${POSTGRES_PASSWORD:-ledger}@db:5432/ledger
      PROVIDER_URL: http://provider:8001
      CALLBACK_URL: http://wallet:8000/v1/webhooks/provider
      PROVIDER_API_KEY: ${PROVIDER_API_KEY:-dev-provider-key}
      WEBHOOK_SECRET: ${WEBHOOK_SECRET:-dev-webhook-secret}
    ports:
      - "8000:8000"
    depends_on:
      db:
        condition: service_healthy
      provider:
        condition: service_started
```

`.env.example`:

```
# Copy to .env to override the dev defaults from docker-compose.yml.
POSTGRES_PASSWORD=ledger
PROVIDER_API_KEY=change-me
WEBHOOK_SECRET=change-me
PUBLIC_URL=http://localhost:8001

# Used by `uv run pytest` on the host.
TEST_DATABASE_URL=postgresql://ledger:ledger@localhost:55432/ledger_test
```

`.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_USER: ledger
          POSTGRES_PASSWORD: ledger
          POSTGRES_DB: ledger_test
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U ledger"
          --health-interval 2s
          --health-timeout 3s
          --health-retries 30
    env:
      TEST_DATABASE_URL: postgresql://ledger:ledger@localhost:5432/ledger_test
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pytest
```

- [ ] **Крок 3: Запустити compose і пройти сценарій**

Якщо працює тимчасовий кластер із передумов, спершу зупинити його, бо він займає порт 55432: `"$PG/pg_ctl" -D "$PGDATA_DIR" stop`.

```bash
docker compose up -d --build --wait
KEY=$(docker compose exec -T wallet python -m wallet.cli create-account alice | awk '/api_key/ {print $2}')
INVOICE=$(curl -s -X POST localhost:8000/v1/deposits -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"amount": "10.00"}' \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["pay_url"].rsplit("/", 1)[1])')
curl -s -X POST "localhost:8001/invoices/$INVOICE/simulate" -H "Authorization: Bearer dev-provider-key" \
  -H "Content-Type: application/json" -d '{"status": "paid", "duplicate": true}'
curl -s localhost:8000/v1/accounts/me -H "X-API-Key: $KEY"
```

Очікувано: `simulate` повертає `"deliveries": [{"status_code": 200}, {"status_code": 200}]`, а `/v1/accounts/me` — `"balance_usd": "9.85"`.

- [ ] **Крок 4: Тести на БД із compose**

```bash
uv run pytest
docker compose down
```

Очікувано: `78 passed`.

- [ ] **Крок 5: Коміт**

```bash
git add Dockerfile .dockerignore docker-compose.yml docker .env.example .github
git commit -m "chore: docker compose for both services and CI with ruff and Postgres tests" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Крок 6: GitHub і перший прогін CI (лише з дозволу людини)**

Контролер питає людину назву репозиторію, видимість (приватний чи публічний) і дозвіл на push. Лише після явного «так»:

```bash
gh repo create <name> --private --source . --remote origin
git push -u origin ledgerlite
gh run watch --exit-status
```

Очікувано: job `test` зелений.

---

### Задача 8: Покупка кредитів

**Файли:**
- Створити: `wallet/purchases.py`, `tests/test_purchases.py`
- Змінити: `wallet/app.py` — додати `purchases` до імпорту і `app.include_router(purchases.router)` після `transfers`

**Інтерфейси:**
- Споживає: `credits_price`, `post_entry`, `current_account_id`.
- Надає: маршрут `POST /v1/purchases`, `MAX_CREDITS_PER_PURCHASE`.

- [ ] **Крок 1: Тест, що падає** — `tests/test_purchases.py`

```python
"""POST /v1/purchases: credits bought with the USD balance."""

from decimal import Decimal

import pytest

pytestmark = pytest.mark.anyio


async def test_purchase_debits_usd_and_adds_credits(app, client, make_account):
    _, headers = await make_account("alice", balance_usd="50.00")
    response = await client.post("/v1/purchases", headers=headers, json={"credits": 507})
    assert response.status_code == 201
    body = response.json()
    assert (body["credits"], body["price"], body["balance_usd"], body["balance_credits"]) == (
        507,
        "48.17",
        "1.83",
        507,
    )
    entry = await app.state.pool.fetchrow("SELECT type, amount_usd, credits FROM ledger_entries")
    assert dict(entry) == {"type": "purchase", "amount_usd": Decimal("-48.17"), "credits": 507}


async def test_purchase_without_enough_usd_is_409(app, client, make_account):
    account_id, headers = await make_account("alice", balance_usd="10.00")
    response = await client.post("/v1/purchases", headers=headers, json={"credits": 333})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "insufficient_funds"
    row = await app.state.pool.fetchrow(
        "SELECT balance_usd, balance_credits FROM accounts WHERE id = $1", account_id
    )
    assert (row["balance_usd"], row["balance_credits"]) == (Decimal("10.00"), 0)


@pytest.mark.parametrize("credits", [True, 507.0, "507", 0, -1, 1_000_000_001])
async def test_credits_must_be_a_positive_whole_number(client, make_account, credits):
    _, headers = await make_account("alice", balance_usd="50.00")
    response = await client.post("/v1/purchases", headers=headers, json={"credits": credits})
    assert response.status_code == 422
```

- [ ] **Крок 2: Переконатися, що тести падають**

Команда: `uv run pytest tests/test_purchases.py -v`
Очікувано: 8 тестів падають із 404 `not_found`.

- [ ] **Крок 3: Реалізація** — `wallet/purchases.py`

```python
"""POST /v1/purchases: buys credits with the USD balance."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, StrictInt

from wallet.auth import current_account_id
from wallet.ledger import post_entry
from wallet.money import credits_price

router = APIRouter()

# Keeps the number within BIGINT: a bigger value would fail in the database as a 500.
MAX_CREDITS_PER_PURCHASE = 1_000_000_000


class PurchaseIn(BaseModel):
    credits: Annotated[StrictInt, Field(ge=1, le=MAX_CREDITS_PER_PURCHASE)]


class PurchaseOut(BaseModel):
    purchase_id: UUID
    credits: int
    price: Decimal
    balance_usd: Decimal
    balance_credits: int


@router.post("/v1/purchases", status_code=201, response_model=PurchaseOut)
async def create_purchase(
    request: Request,
    body: PurchaseIn,
    account_id: Annotated[UUID, Depends(current_account_id)],
) -> PurchaseOut:
    price = credits_price(body.credits)
    purchase_id = uuid4()
    async with request.app.state.pool.acquire() as conn, conn.transaction():
        balances = await post_entry(
            conn,
            account_id=account_id,
            entry_type="purchase",
            amount_usd=-price,
            credits=body.credits,
            ref_id=purchase_id,
        )
    return PurchaseOut(
        purchase_id=purchase_id, credits=body.credits, price=price, **dict(balances)
    )
```

У `wallet/app.py` додати `purchases` до імпорту і `app.include_router(purchases.router)` після `transfers`.

- [ ] **Крок 4: Переконатися, що тести проходять**

Команда: `uv run pytest -v`
Очікувано: `86 passed`.

- [ ] **Крок 5: Лінтер і коміт**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest
git add wallet tests
git commit -m "feat: POST /v1/purchases with tiered credits pricing" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Задача 9: Історія операцій

**Файли:**
- Створити: `wallet/transactions.py`, `tests/test_transactions.py`
- Змінити: `wallet/app.py` — додати `transactions` до імпорту і `app.include_router(transactions.router)` після `purchases`

**Інтерфейси:**
- Споживає: `current_account_id`, таблицю `ledger_entries`.
- Надає: маршрут `GET /v1/transactions?limit=&cursor=`.

- [ ] **Крок 1: Тест, що падає** — `tests/test_transactions.py`

```python
"""GET /v1/transactions: own history, newest first, with keyset pagination."""

import pytest

pytestmark = pytest.mark.anyio


async def buy_one_credit(client, headers):
    response = await client.post("/v1/purchases", headers=headers, json={"credits": 1})
    assert response.status_code == 201


async def history(client, headers, **params):
    response = await client.get("/v1/transactions", headers=headers, params=params)
    assert response.status_code == 200
    return response.json()


async def test_history_is_paginated_newest_first(client, make_account):
    _, headers = await make_account("alice", balance_usd="1.00")
    for _ in range(3):
        await buy_one_credit(client, headers)
    first = await history(client, headers, limit=2)
    assert [item["amount_usd"] for item in first["items"]] == ["-0.10", "-0.10"]
    assert first["items"][0]["id"] > first["items"][1]["id"]
    second = await history(client, headers, limit=2, cursor=first["next_cursor"])
    assert len(second["items"]) == 1
    assert second["items"][0]["id"] < first["items"][1]["id"]
    assert second["next_cursor"] is None


async def test_history_shows_only_own_entries(client, make_account):
    _, alice = await make_account("alice", balance_usd="1.00")
    _, bob = await make_account("bob", balance_usd="1.00")
    await buy_one_credit(client, bob)
    assert await history(client, alice) == {"items": [], "next_cursor": None}


@pytest.mark.parametrize("params", [{"cursor": "abc"}, {"limit": 0}, {"limit": 101}])
async def test_bad_paging_parameters_are_422(client, make_account, params):
    _, headers = await make_account()
    response = await client.get("/v1/transactions", headers=headers, params=params)
    assert response.status_code == 422
```

- [ ] **Крок 2: Переконатися, що тести падають**

Команда: `uv run pytest tests/test_transactions.py -v`
Очікувано: 5 тестів падають (`assert 404 == 200` і `assert 404 == 422`).

- [ ] **Крок 3: Реалізація** — `wallet/transactions.py`

```python
"""GET /v1/transactions: the caller's history, newest first, with keyset pagination."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from wallet.auth import current_account_id

router = APIRouter()


class EntryOut(BaseModel):
    id: int
    type: str
    amount_usd: Decimal
    credits: int
    ref_id: UUID
    counterparty_id: UUID | None
    created_at: datetime


class EntriesPage(BaseModel):
    items: list[EntryOut]
    next_cursor: str | None


@router.get("/v1/transactions", response_model=EntriesPage)
async def list_transactions(
    request: Request,
    account_id: Annotated[UUID, Depends(current_account_id)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(pattern=r"^[0-9]{1,18}$")] = None,
) -> EntriesPage:
    # One extra row only tells us whether another page exists.
    rows = await request.app.state.pool.fetch(
        """
        SELECT id, type, amount_usd, credits, ref_id, counterparty_id, created_at
        FROM ledger_entries
        WHERE account_id = $1 AND ($2::bigint IS NULL OR id < $2)
        ORDER BY id DESC
        LIMIT $3
        """,
        account_id,
        int(cursor) if cursor else None,
        limit + 1,
    )
    page = rows[:limit]
    next_cursor = str(page[-1]["id"]) if len(rows) > limit else None
    return EntriesPage(items=[EntryOut(**dict(row)) for row in page], next_cursor=next_cursor)
```

У `wallet/app.py` додати `transactions` до імпорту і `app.include_router(transactions.router)` після `purchases`.

- [ ] **Крок 4: Переконатися, що тести проходять**

Команда: `uv run pytest -v`
Очікувано: `91 passed`.

- [ ] **Крок 5: Лінтер і коміт**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest
git add wallet tests
git commit -m "feat: GET /v1/transactions with keyset pagination" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Задача 10: Звірка (бонус)

**Файли:**
- Створити: `tests/test_reconcile.py`
- Змінити: `wallet/provider.py` (додати `get_invoice`), `wallet/deposits.py` (додати `reconcile`), `wallet/cli.py` (весь файл)

**Інтерфейси:**
- Споживає: `apply_invoice_status`, `FINAL_STATUSES`, `parse_amount`, `new_provider_client`, `create_pool`.
- Надає: `wallet.provider.get_invoice(client, invoice_id: str) -> dict` з ключами `status` і `paid_amount`; `wallet.deposits.reconcile(pool, client, older_than_minutes: int) -> dict[str, int]` з ключами `resolved`, `pending`, `failed`; команду `python -m wallet.cli reconcile --older-than N`.

- [ ] **Крок 1: Тест, що падає** — `tests/test_reconcile.py`

```python
"""Reconciliation: pending deposits whose webhook never arrived."""

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from wallet.deposits import reconcile
from wallet.provider import new_provider_client

pytestmark = pytest.mark.anyio

PROVIDER_ANSWERS = {
    "/invoices/inv_paid": {"status": "paid", "paid_amount": "10.00"},
    "/invoices/inv_waiting": {"status": "pending", "paid_amount": "0.00"},
}


async def add_pending_deposit(app, account_id, invoice_id, minutes_ago):
    await app.state.pool.execute(
        """
        INSERT INTO deposits (id, account_id, amount, status, invoice_id, pay_url, created_at)
        VALUES ($1, $2, 10.00, 'pending', $3, 'http://provider.test/pay',
                now() - make_interval(mins => $4::int))
        """,
        uuid4(),
        account_id,
        invoice_id,
        minutes_ago,
    )


async def test_reconcile_settles_old_pending_deposits(app, settings, make_account):
    account_id, _ = await make_account()
    for invoice_id in ("inv_paid", "inv_waiting", "inv_broken"):
        await add_pending_deposit(app, account_id, invoice_id, minutes_ago=20)
    await add_pending_deposit(app, account_id, "inv_fresh", minutes_ago=1)
    asked = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path in PROVIDER_ANSWERS:
            return httpx.Response(200, json=PROVIDER_ANSWERS[request.url.path])
        return httpx.Response(500)

    async with new_provider_client(settings, httpx.MockTransport(answer)) as client:
        counts = await reconcile(app.state.pool, client, older_than_minutes=15)

    assert counts == {"resolved": 1, "pending": 1, "failed": 1}
    assert "/invoices/inv_fresh" not in asked
    assert await app.state.pool.fetchval("SELECT balance_usd FROM accounts") == Decimal("9.85")
    rows = await app.state.pool.fetch("SELECT invoice_id, status FROM deposits")
    assert {row["invoice_id"]: row["status"] for row in rows} == {
        "inv_paid": "paid",
        "inv_waiting": "pending",
        "inv_broken": "pending",
        "inv_fresh": "pending",
    }
```

- [ ] **Крок 2: Переконатися, що тест падає**

Команда: `uv run pytest tests/test_reconcile.py -v`
Очікувано: збій збирання — `ImportError: cannot import name 'reconcile' from 'wallet.deposits'`.

- [ ] **Крок 3: Реалізація**

У `wallet/provider.py` дописати після `create_invoice`:

```python
async def get_invoice(client: httpx.AsyncClient, invoice_id: str) -> dict:
    return await _call(client, "GET", f"/invoices/{invoice_id}", required=("status", "paid_amount"))
```

У `wallet/deposits.py` додати імпорти `logging`, `httpx`, `from wallet.money import parse_amount` (до наявного імпорту з `wallet.money`), рядок `logger = logging.getLogger(__name__)` під `router` і дописати в кінець файлу:

```python
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
```

`wallet/cli.py` (весь файл):

```python
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
```

- [ ] **Крок 4: Переконатися, що тести проходять**

Команда: `uv run pytest -v`
Очікувано: `92 passed`.

- [ ] **Крок 5: Лінтер і коміт**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest
git add wallet tests
git commit -m "feat: reconcile command for deposits whose webhook was lost" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Задача 11: Rate limit (бонус)

**Файли:**
- Створити: `wallet/ratelimit.py`, `tests/test_ratelimit.py`
- Змінити: `wallet/deposits.py`, `wallet/transfers.py`, `wallet/purchases.py` — у кожному імпорт `from wallet.auth import current_account_id` замінити на `from wallet.ratelimit import rate_limited_account_id`, а `Depends(current_account_id)` у POST-маршруті — на `Depends(rate_limited_account_id)`

**Інтерфейси:**
- Споживає: `current_account_id`, `ApiError`.
- Надає: `wallet.ratelimit.RATE_LIMIT_PER_MINUTE = 30`, залежність `rate_limited_account_id(...) -> UUID`.

- [ ] **Крок 1: Тест, що падає** — `tests/test_ratelimit.py`

```python
"""Rate limit: at most 30 POST requests a minute per API key."""

import pytest

pytestmark = pytest.mark.anyio


async def fill_current_window(app, account_id, count):
    # Seeding the counter keeps the tests away from a minute boundary in the middle of a burst.
    await app.state.pool.execute(
        """
        INSERT INTO rate_limits (account_id, window_start, count)
        VALUES ($1, date_trunc('minute', now()), $2)
        """,
        account_id,
        count,
    )


async def buy(client, headers):
    return await client.post("/v1/purchases", headers=headers, json={"credits": 1})


async def test_thirty_first_post_in_a_minute_is_429(app, client, make_account):
    account_id, headers = await make_account()
    await fill_current_window(app, account_id, 30)
    response = await buy(client, headers)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(response.headers["Retry-After"]) <= 60


async def test_thirtieth_post_still_passes(app, client, make_account):
    account_id, headers = await make_account()
    await fill_current_window(app, account_id, 29)
    assert (await buy(client, headers)).status_code == 409  # reached the endpoint: no USD


async def test_every_post_counts_even_when_it_fails(app, client, make_account):
    account_id, headers = await make_account()
    await buy(client, headers)
    await client.post("/v1/deposits", headers=headers, json={"amount": 10.5})
    counted = await app.state.pool.fetchval(
        "SELECT sum(count) FROM rate_limits WHERE account_id = $1", account_id
    )
    assert counted == 2


async def test_limit_is_per_api_key(app, client, make_account):
    alice, _ = await make_account("alice")
    _, bob_headers = await make_account("bob")
    await fill_current_window(app, alice, 30)
    assert (await buy(client, bob_headers)).status_code == 409


async def test_reads_are_not_limited(app, client, make_account):
    account_id, headers = await make_account()
    await fill_current_window(app, account_id, 30)
    assert (await client.get("/v1/accounts/me", headers=headers)).status_code == 200


async def test_provider_webhook_is_not_limited(client):
    responses = [await client.post("/v1/webhooks/provider", content=b"{}") for _ in range(31)]
    assert {r.status_code for r in responses} == {401}
```

- [ ] **Крок 2: Переконатися, що тести падають**

Команда: `uv run pytest tests/test_ratelimit.py -v`
Очікувано: падають `test_thirty_first_post_in_a_minute_is_429` (`assert 409 == 429`) і `test_every_post_counts_even_when_it_fails` (`assert None == 2`); решта чотири проходять, бо описують поведінку, яка вже правильна.

- [ ] **Крок 3: Реалізація** — `wallet/ratelimit.py`

```python
"""At most 30 POST requests a minute per API key."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request

from wallet.auth import current_account_id
from wallet.errors import ApiError

RATE_LIMIT_PER_MINUTE = 30


async def rate_limited_account_id(
    request: Request, account_id: Annotated[UUID, Depends(current_account_id)]
) -> UUID:
    # shortcut: a fixed one-minute window lets up to twice the limit through around a window
    # boundary; switch to a sliding window if that starts to matter.
    # shortcut: old windows are never deleted; add a cleanup job when the table grows.
    row = await request.app.state.pool.fetchrow(
        """
        INSERT INTO rate_limits (account_id, window_start, count)
        VALUES ($1, date_trunc('minute', now()), 1)
        ON CONFLICT (account_id, window_start) DO UPDATE SET count = rate_limits.count + 1
        RETURNING count,
                  ceil(extract(epoch FROM window_start + interval '1 minute' - now()))::int
                      AS seconds_left
        """,
        account_id,
    )
    if row["count"] > RATE_LIMIT_PER_MINUTE:
        raise ApiError(
            429,
            "rate_limited",
            "Too many POST requests, retry later",
            {"limit_per_minute": RATE_LIMIT_PER_MINUTE},
            headers={"Retry-After": str(max(row["seconds_left"], 1))},
        )
    return account_id
```

Потім у `wallet/deposits.py`, `wallet/transfers.py` і `wallet/purchases.py` замінити імпорт і залежність, як описано у «Файлах».

- [ ] **Крок 4: Переконатися, що тести проходять**

Команда: `uv run pytest -v`
Очікувано: `98 passed`.

- [ ] **Крок 5: Лінтер і коміт**

```bash
uv run ruff format . && uv run ruff check . && uv run pytest
git add wallet tests
git commit -m "feat: rate limit of 30 POST requests a minute per API key" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Задача 12: README і AI_NOTES

**Файли:**
- Створити: `README.md`, `AI_NOTES.md`

**Інтерфейси:**
- Споживає: усе зроблене, історію комітів.
- Надає: документацію для рев'юерів.

- [ ] **Крок 1: Перевірити сценарій README на compose**

Підняти `docker compose up -d --build --wait`, пройти всі команди з розділу «Сценарій перевірки» нижче й підставити справжні значення в очікувані результати. Потім `docker compose down`.

- [ ] **Крок 2: `README.md`**

````markdown
# LedgerLite

Тестове завдання «AI Software Engineer (Python)»: два сервіси, які піднімає одна команда.

- **wallet** (`:8000`) — рахунки користувачів у USD і кредитах: депозити через провайдера, перекази, покупка кредитів, історія операцій.
- **provider** (`:8001`) — імітація платіжного провайдера: інвойси в пам'яті та підписані вебхуки.

## Запуск

```bash
docker compose up --build
```

Документація API: <http://localhost:8000/docs> (wallet) і <http://localhost:8001/docs> (провайдер). У `docker-compose.yml` змінні оточення мають dev-значення; щоб їх змінити, скопіюйте `.env.example` у `.env`.

## Сценарій перевірки

```bash
docker compose exec wallet python -m wallet.cli create-account alice   # друкує account_id і api_key
docker compose exec wallet python -m wallet.cli create-account bob
KEY=<api_key alice>

# депозит: wallet створює інвойс у провайдера
curl -s -X POST localhost:8000/v1/deposits -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"amount": "10.00"}'

# оплата з дублем вебхука; <invoice_id> — останній сегмент pay_url
curl -s -X POST localhost:8001/invoices/<invoice_id>/simulate \
  -H "Authorization: Bearer dev-provider-key" -H "Content-Type: application/json" \
  -d '{"status": "paid", "duplicate": true}'

curl -s localhost:8000/v1/accounts/me -H "X-API-Key: $KEY"   # balance_usd "9.85": дубль не зарахувався

curl -s -X POST localhost:8000/v1/transfers -H "X-API-Key: $KEY" -H "Idempotency-Key: t-1" \
  -H "Content-Type: application/json" -d '{"to_account_id": "<account_id bob>", "amount": "5.00"}'
curl -s -X POST localhost:8000/v1/purchases -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"credits": 30}'
curl -s "localhost:8000/v1/transactions?limit=10" -H "X-API-Key: $KEY"

# звірка депозитів, вебхук яких загубився
docker compose exec wallet python -m wallet.cli reconcile --older-than 15
```

## Тести й лінтер

```bash
docker compose up -d db
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Тести працюють зі справжнім PostgreSQL: `localhost:55432/ledger_test`, адресу можна змінити через `TEST_DATABASE_URL`. Той самий набір запускає CI (`.github/workflows/ci.yml`).

## Хто кого викликає

```
клієнт ──X-API-Key──▶ wallet :8000 ──Bearer + таймаути──▶ provider-mock :8001
                         ▲                                        │
                         └── вебхук (X-Signature, X-Timestamp) ───┘
wallet ──▶ PostgreSQL
```

```mermaid
sequenceDiagram
    participant C as Клієнт
    participant W as wallet
    participant P as provider-mock
    participant D as PostgreSQL
    C->>W: POST /v1/deposits
    W->>P: POST /invoices (таймаути 2/5 с)
    P-->>W: invoice_id, pay_url
    W->>D: депозит pending
    W-->>C: 201 + pay_url
    Note over P: POST /invoices/{id}/simulate
    P->>W: POST /v1/webhooks/provider (підпис)
    W->>D: FOR UPDATE депозиту, зарахування за мінусом комісії
    W-->>P: 200 applied / ignored
```

## Чому FastAPI і asyncpg

FastAPI дає валідацію через Pydantic, async-виклики провайдера й OpenAPI без додаткового коду. Asyncpg із SQL вручну замість ORM — бо грошові операції тут складаються з кількох запитів із явними `FOR UPDATE`, `ON CONFLICT` і умовним `UPDATE`, і ці гарантії видно в коді саме так, як вони виконуються.

## Ключові рішення та компроміси

1. **Баланс змінює одна функція.** `ledger.post_entry()` робить умовний `UPDATE … WHERE balance_usd + $2 >= 0` і в тій самій транзакції пише запис в історію. `CHECK (balance_usd >= 0)` — остання страховка.
2. **Вебхук.** Підпис перевіряється по сирих байтах тіла до парсингу, через `hmac.compare_digest`; свіжість — лише за `X-Timestamp`, бо запізнілий вебхук законний. Дубль і пізній вебхук зупиняє перехід `pending → фінальний` під `FOR UPDATE`, тож окрема таблиця подій не потрібна, а `event_id` лишається в `closed_by_event_id`. На дубль відповідаємо 200, щоб провайдер не повторював його.
3. **Ідемпотентність переказів.** Ключ діє в межах користувача й захоплюється `INSERT … ON CONFLICT` у тій самій транзакції, що й переказ. Паралельний повтор чекає на унікальному індексі й отримує збережену відповідь. Зберігаються точні байти відповіді (TEXT, не JSONB, бо JSONB переставляє ключі). Бізнес-помилки 404/409 теж зберігаються, 500 — ні.
4. **Без взаємоблокувань.** Обидва рахунки переказу блокуються одним запитом у порядку `id`, тож зустрічні перекази не чекають один на одного по колу.
5. **Провайдер.** Виклик іде поза транзакцією БД, з таймаутами 2 с на з'єднання і 5 с на відповідь. Таймаут → 504, недоступність → 503, погана відповідь → 502, кожен зі своїм `code`. Депозит записується лише після відповіді провайдера, ретраїв немає, бо `POST /invoices` не ідемпотентний.
6. **API-ключі.** У БД лежить sha256 від випадкового 256-бітного ключа. Bcrypt тут не потрібен і не дав би знайти рахунок за ключем одним запитом.
7. **Бонуси без нової інфраструктури.** Rate limit — лічильник у Postgres, звірка — CLI-команда, яка викликає ту саму функцію переходу статусу, що й вебхук.

## Що спрощено або не зроблено

- Міграцій немає: `schema.sql` застосовується під час кожного старту й безпечний лише для одного процесу wallet.
- Ключі ідемпотентності та вікна rate limit ніколи не видаляються.
- Rate limit на фіксованому вікні: на стику хвилин пропускає до 60 запитів.
- Покупки й депозити без `Idempotency-Key`: ТЗ вимагає його лише для переказів.
- Вебхук, що прийшов раніше, ніж wallet записав депозит, отримує 404; справжній провайдер повторив би його, мок — ні.
- Якщо провайдер створив інвойс, а запис депозиту впав, інвойс лишається сиротою (клієнт не отримав `pay_url`).
- Звірка запускається вручну, розклад (cron) не налаштований.
- Мок повертає помилки у стандартному форматі FastAPI, а не в єдиному форматі wallet.
- Наслідок правил ціни: 500 кредитів коштують менше за 499, а 1000 — менше за 999.
````

- [ ] **Крок 3: `AI_NOTES.md` (до однієї сторінки)**

Зібрати факти: `git log --grep "AI_NOTES:" --format="%h %s%n%b"`. Контролер пише чернетку з такими розділами й віддає людині на перевірку розділ «Що вирішив сам» і рядок про час:

```markdown
# AI_NOTES

## Інструменти
<які AI-інструменти й скіли використовувались і для чого — по одному рядку>

## Що вирішив сам, а не AI
<рішення людини з brainstorming: підхід, обсяг бонусів, мова документації, погодження кожної секції дизайну, правки>

## Де згенерований код був неправильним або неоптимальним
<щонайменше 3 випадки з git log: що було не так, як помітили, що виправили — з хешем коміту>

## Скільки часу
<оцінка людини>
```

- [ ] **Крок 4: Коміт**

```bash
git add README.md AI_NOTES.md
git commit -m "docs: README and AI_NOTES" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Задача 13: Фінальне рев'ю і завершення гілки

**Файли:**
- Змінити: лише ті, де рев'ю знайде підтверджені проблеми

**Інтерфейси:**
- Споживає: увесь репозиторій.
- Надає: перевірену гілку, злиту в `main` і запушену з дозволу людини.

- [ ] **Крок 1: Рев'ю агентами**

Паралельно запустити агентів PR Review Toolkit (`code-reviewer`, `silent-failure-hunter`, `pr-test-analyzer`, `type-design-analyzer`, `comment-analyzer`) з їхніми промптами з `~/.claude/plugins/marketplaces/claude-plugins-official/plugins/pr-review-toolkit/agents/` на весь код гілки. Додатково провести `ponytail-review` і окремий spec-прохід за `code-quality`: субагент отримує лише специфікацію й код.

- [ ] **Крок 2: Перевірити кожну знахідку**

Кожну знахідку звірити з кодом: прочитати місце або відтворити тестом. Підтверджені виправити через TDD, окремими комітами; якщо це помилка AI, додати рядок `AI_NOTES:`. Непідтверджені записати з причиною.

- [ ] **Крок 3: Спрощення**

Агент `code-simplifier` з PR Review Toolkit — лише на змінах після виправлень, без зміни поведінки.

- [ ] **Крок 4: Свіжа повна перевірка**

```bash
uv run ruff format --check . && uv run ruff check . && uv run pytest
docker compose down
git clone --branch ledgerlite . "$CLONE_DIR"   # CLONE_DIR — порожня тимчасова папка
docker compose -f "$CLONE_DIR/docker-compose.yml" up -d --build --wait   # і сценарій із README
docker compose -f "$CLONE_DIR/docker-compose.yml" down
```

Чистий клон доводить, що для запуску вистачає того, що є в git: без локального `.env`, `.venv` і незакомічених файлів.

- [ ] **Крок 5: Завершити гілку (superpowers:finishing-a-development-branch)**

Запропонувати людині варіанти: створити `main` на вершині `ledgerlite` і запушити, відкрити PR або залишити як є. Push і зміна гілки за замовчуванням на GitHub — лише після явного «так»:

```bash
git branch main ledgerlite
git push -u origin main
gh repo edit --default-branch main
gh run watch --exit-status
```
