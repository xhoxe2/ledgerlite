# LedgerLite: дизайн

Дата: 2026-10-21. Статус: усі секції погоджено в чаті, документ чекає фінального рев'ю.
Джерело вимог: тестове завдання «AI Software Engineer (Python)».

## 1. Мета й обсяг

Два сервіси, які піднімає одна команда `docker compose up`:

- **provider-mock** імітує платіжного провайдера, дані тримає в пам'яті;
- **wallet-service** веде рахунки користувачів у USD і кредитах: депозити через провайдера, перекази, покупка кредитів, історія операцій.

Обсяг: усе обов'язкове з ТЗ і обидва бонуси. Порядок реалізації повторює пріоритети ТЗ:

1. гроші та вебхук;
2. перекази з ідемпотентністю;
3. Docker і CI;
4. покупки та історія операцій;
5. бонуси: звірка й rate limit.

Свідомо не робимо (кожен пункт потрапить у README):

- ретраї `POST /invoices`: інвойс у провайдера не ідемпотентний, і повтор міг би створити другий;
- `Idempotency-Key` для покупок і депозитів: ТЗ вимагає його лише для переказів;
- інструмент міграцій: `schema.sql` застосовується під час старту wallet;
- очищення старих ключів ідемпотентності та вікон rate limit;
- окремі таблиці `users`, `api_keys`, `webhook_events`, `transfers`, `purchases` (чому — у розділі 4).

## 2. Стек

| Що | Вибір | Чому |
|---|---|---|
| Мова | Python 3.12 | ТЗ вимагає 3.11+, для 3.12 є готові колеса всіх залежностей |
| HTTP | FastAPI + uvicorn | валідація через Pydantic, async для викликів провайдера, OpenAPI для рев'юерів |
| БД | PostgreSQL 17 + asyncpg, SQL вручну | грошові операції — це кілька запитів з явними `FOR UPDATE`, `ON CONFLICT` і умовним `UPDATE`; ORM сховав би саме цю семантику |
| HTTP-клієнт | httpx | таймаути з коробки; `MockTransport` для тестів без нових залежностей |
| Тести | pytest + плагін anyio | anyio вже приходить разом з httpx, тому pytest-asyncio і respx не потрібні |
| Лінтер | ruff (`check` і `format --check`) | |
| Пакети | uv, `uv.lock` у репозиторії | |

Не використовуємо ORM, Alembic і pydantic-settings: змінні оточення читаються через `os.environ`.

## 3. Архітектура

```
клієнт ──X-API-Key──▶ wallet :8000 ──Bearer + таймаути──▶ provider-mock :8001
                         ▲                                        │
                         └── вебхук (X-Signature, X-Timestamp) ───┘
тестер ──Bearer──▶ provider-mock POST /invoices/{id}/simulate
wallet ──▶ PostgreSQL
```

Код розкладено за фічами: файли, що змінюються разом, лежать разом.

| Файл | Відповідальність |
|---|---|
| `provider_mock/app.py` | увесь мок: сховище в пам'яті, маршрути, підпис і надсилання вебхука |
| `wallet/app.py` | `create_app()`: lifespan (пул asyncpg, httpx-клієнт провайдера, `schema.sql`), обробники помилок, роутери |
| `wallet/config.py` | `Settings` із `os.environ` |
| `wallet/schema.sql` | схема БД |
| `wallet/errors.py` | `ApiError` і єдиний формат помилок |
| `wallet/money.py` | типи `PositiveMoney` і `NonNegativeMoney`, комісія, ціна кредитів; лише чисті функції |
| `wallet/auth.py` | генерація й хеш API-ключа, залежність `current_account_id` |
| `wallet/ledger.py` | `post_entry()`: єдине місце, де змінюється баланс, завжди разом із записом в історії |
| `wallet/provider.py` | контракт провайдера: `create_invoice`, `get_invoice`, `verify_signature` |
| `wallet/idempotency.py` | `request_hash`, `claim`, `save` |
| `wallet/ratelimit.py` | залежність `rate_limited_account_id` |
| `wallet/accounts.py` | `GET /v1/accounts/me` |
| `wallet/deposits.py` | `POST /v1/deposits`, `POST /v1/webhooks/provider`, `apply_invoice_status()`, `reconcile()` |
| `wallet/transfers.py` | `POST /v1/transfers` |
| `wallet/purchases.py` | `POST /v1/purchases` |
| `wallet/transactions.py` | `GET /v1/transactions` |
| `wallet/cli.py` | команди `create-account` і `reconcile` |

Один `Dockerfile` (python:3.12-slim + uv) на обидва сервіси. Compose запускає `db` (postgres:17-alpine з healthcheck), `provider` і `wallet`; wallet чекає, доки БД стане здоровою. Wallet стартує командою `uvicorn --factory wallet.app:create_app`, provider — `uvicorn provider_mock.app:app`.

### Конфігурація

| Змінна | Сервіс | Dev-значення в compose |
|---|---|---|
| `DATABASE_URL` | wallet | `postgresql://ledger:ledger@db:5432/ledger` |
| `PROVIDER_URL` | wallet | `http://provider:8001` |
| `CALLBACK_URL` | wallet | `http://wallet:8000/v1/webhooks/provider` |
| `PROVIDER_API_KEY` | обидва | `dev-provider-key` |
| `WEBHOOK_SECRET` | обидва | `dev-webhook-secret` |
| `PUBLIC_URL` | provider | `http://localhost:8001` (з нього складається `pay_url`) |
| `POSTGRES_PASSWORD` | db | `ledger` |

У compose змінні мають dev-значення за замовчуванням (`${VAR:-…}`), тому на чистому клоні `docker compose up` працює без `.env`. Усі змінні перелічено в `.env.example`. Справжніх секретів у репозиторії немає. Порт БД назовні не відкривається.

## 4. Модель даних

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
    response     JSONB,
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

Рішення:

- Усі UUID генерує Python (`uuid4`). Ідентифікатор депозиту потрібен ще до запису в БД, бо він іде провайдеру як `external_id`, тож решта таблиць робить так само.
- Баланс зберігається в `accounts` і змінюється лише через `ledger.post_entry()`, у тій самій транзакції, що й запис у `ledger_entries`. `CHECK (balance_usd >= 0)` лишається останньою страховкою.
- `users` і `api_keys` не потрібні: у користувача один ключ, тому хеш ключа лежить в `accounts`.
- `transfers` і `purchases` не потрібні: обидві ноги переказу й покупка є записами в `ledger_entries` зі спільним `ref_id`.
- `webhook_events` не потрібна. Статуси провайдера фінальні, тож і дубль із тим самим `event_id`, і пізній вебхук упираються в один перехід `pending → фінальний` під `FOR UPDATE`. `event_id`, який закрив депозит, лишається в `closed_by_event_id` для аудиту.
- Статусу `failed` немає: депозит пишеться в БД лише після того, як провайдер повернув інвойс.
- `idempotency_keys.status_code` і `response` заповнюються в тій самій транзакції, що й вставка ключа, тож інші транзакції ніколи не бачать там `NULL`.
- Ключі ідемпотентності та вікна rate limit ніколи не видаляються; у коді це позначено коментарем `shortcut:`.

## 5. Гроші

- Суми в JSON — рядки, у коді — `Decimal`, у БД — `NUMERIC(14, 2)`; asyncpg сам повертає `Decimal`. Кредити — цілі числа, у БД `BIGINT`.
- Вхідна сума проходить `BeforeValidator`, який приймає лише `str` за regex `^(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?$`. Саме `[0-9]`, а не `\d`, бо `\d` пропускає юнікод-цифри на кшталт «٣», а `Decimal` їх приймає. Число замість рядка, `"1e2"`, `" 5.00 "`, `"10.001"`, `"-1"`, `"01.00"` дають 422.
- `PositiveMoney` — сума > 0 (депозит, переказ; мінімум 0.01). `NonNegativeMoney` — сума ≥ 0 (`paid_amount` у вебхуку).
- Кредити в покупці — `StrictInt` від 1 до 1 000 000 000. Верхня межа не дає величезному числу впасти в 500 на `BIGINT`. `true`, `507.0` і `"507"` дають 422.
- Комісія провайдера: `fee = (paid × 0.015)`, округлення до центів за `ROUND_HALF_UP`; зараховуємо `paid − fee`. Режим округлення завжди передається явно: `quantize()` без нього бере `ROUND_HALF_EVEN`.

  | Сплачено | Комісія | Зараховано | Звідки |
  |---|---|---|---|
  | 10.00 | 0.15 | 9.85 | ТЗ |
  | 0.99 | 0.01 | 0.98 | ТЗ |
  | 33.33 | 0.50 | 32.83 | ТЗ |
  | 1.00 | 0.02 | 0.98 | наш: float дає 0.01 |
  | 3.00 | 0.05 | 2.95 | наш: `ROUND_HALF_EVEN` дає 0.04 |

- Ціна кредитів: `credits × 0.10 × k`, де `k = 0.875` від 1000 кредитів, `k = 0.95` від 500, інакше `k = 1`. Пороги включні, знижка діє на всю суму, округлення лише фінальної ціни за `ROUND_HALF_UP`.

  | Кредитів | Ціна | Звідки |
  |---|---|---|
  | 333 | 33.30 | ТЗ |
  | 507 | 48.17 | ТЗ: float і `ROUND_HALF_EVEN` дають 48.16 |
  | 1005 | 87.94 | ТЗ |
  | 499 | 49.90 | наш: межа знижки |
  | 500 | 47.50 | наш: межа знижки |
  | 999 | 94.91 | наш: `ROUND_HALF_EVEN` дає 94.90 |
  | 1000 | 87.50 | наш: межа знижки |

  Наслідок правил, який варто згадати в README: 500 кредитів коштують менше за 499, а 1000 — менше за 999.

- Перекази без комісії, мінімум 0.01.

## 6. Потоки

Однаковий ритм для кожного endpoint-а: залежності (ключ, rate limit) → Pydantic-модель тіла → `async with conn.transaction()` → SQL або `post_entry()` → Pydantic-модель відповіді. Помилки повертаються лише через `ApiError`. Моделі відповіді обов'язкові: у звичайному `dict` FastAPI перетворює `Decimal` на float (перевіримо це тестом).

### 6.1 Автентифікація

- Ключ має вигляд `ll_` + `secrets.token_urlsafe(32)`. У БД зберігається sha256-хеш у hex. Для випадкового ключа цього достатньо, а bcrypt не дав би знайти рахунок за ключем одним запитом.
- `X-API-Key` хешується, рахунок шукається за хешем. Ключа немає або він невірний → 401 `unauthorized`.
- Команда `python -m wallet.cli create-account <name>` створює рахунок і один раз друкує `account_id` і ключ.

### 6.2 `GET /v1/accounts/me`

Відповідь 200: `{"account_id", "name", "balance_usd": "9.85", "balance_credits": 507}`.

### 6.3 `POST /v1/deposits`

Тіло: `{"amount": "10.00"}`, сума — `PositiveMoney`.

1. `deposit_id = uuid4()`.
2. `provider.create_invoice()` надсилає `POST /invoices` з `amount`, `currency: "USD"`, `external_id = deposit_id` і `callback_url = CALLBACK_URL`. Таймаут: 2 с на з'єднання, 5 с на відповідь. Під час виклику транзакція БД не відкрита.
3. Помилки провайдера:
   - таймаут → 504 `provider_timeout`;
   - немає з'єднання → 503 `provider_unavailable`;
   - відповідь 4xx/5xx або тіло без потрібних полів → 502 `provider_error`.

   `httpx.TimeoutException` є підкласом `httpx.TransportError`, тож таймаут перехоплюється першим.
4. Після успіху депозит записується зі статусом `pending`. Відповідь 201: `{"deposit_id", "status": "pending", "amount", "pay_url"}`.

### 6.4 `POST /v1/webhooks/provider`

1. Спершу перевіряється підпис по сирому тілу (`await request.body()`), до будь-якого парсингу:
   - обидва заголовки на місці, `X-Signature` починається з `sha256=`, `X-Timestamp` — ціле число;
   - `|now − X-Timestamp| ≤ 300` в обидва боки;
   - `hmac.compare_digest(hex, HMAC-SHA256(WEBHOOK_SECRET, f"{timestamp}." + raw_body))`.

   Будь-яка помилка → 401 `invalid_signature`. Свіжість перевіряємо лише за `X-Timestamp`: `occurred_at` може бути давнім, бо вебхук законно приходить із запізненням.
2. Потім JSON розбирається в модель `{event_id, invoice_id, status ∈ {paid, partially_paid, expired}, paid_amount: NonNegativeMoney, occurred_at}`. Невалідне тіло → 422, тіло не JSON → 400 `invalid_json`.
3. `apply_invoice_status(conn, invoice_id, status, paid_amount, event_id)` в одній транзакції:
   - `SELECT … FROM deposits WHERE invoice_id = $1 FOR UPDATE`;
   - депозиту немає → 404 `unknown_invoice` (справжній провайдер надішле вебхук ще раз);
   - депозит уже закритий (дубль або пізній вебхук) → 200 `{"result": "ignored"}`, стан не змінюється;
   - `paid` або `partially_paid` → `fee`, `credited = paid_amount − fee`, `post_entry(+credited, type="deposit", ref_id=deposit_id)`, якщо `credited > 0`; депозит отримує статус, `paid_amount`, `fee`, `credited`, `closed_by_event_id`, `closed_at` → 200 `{"result": "applied"}`;
   - `expired` → депозит закривається без зарахування → 200 `{"result": "applied"}`.

   Зараховуємо фактично сплачену суму, навіть якщо вона не збігається із сумою інвойсу. Паралельні дублі чекають один на одного на `FOR UPDATE`, і другий бачить уже закритий депозит.

### 6.5 Звірка (бонус)

Команда `python -m wallet.cli reconcile --older-than 15` (хвилини, за замовчуванням 15):

1. бере депозити в `pending`, старші за N хвилин;
2. для кожного викликає `provider.get_invoice()`;
3. фінальний статус передає в ту саму `apply_invoice_status(..., event_id=None)`, статус `pending` пропускає;
4. помилку по одному інвойсу логує й рахує, решту обробляє далі;
5. друкує `resolved=X pending=Y failed=Z`; якщо `failed > 0`, код виходу 1.

Перегони з вебхуком безпечні: обидва шляхи проходять через один `FOR UPDATE`.

### 6.6 `POST /v1/transfers`

Тіло: `{"to_account_id": UUID, "amount": PositiveMoney}`, заголовок `Idempotency-Key` обов'язковий.

1. Ключа немає, він порожній або довший за 255 символів → 400 `invalid_idempotency_key`. Код 400, а не 422, як радить чернетка IETF про цей заголовок. Переказ самому собі → 422 `same_account`. Обидві перевірки йдуть до ідемпотентності, тож під ключем не зберігаються.
2. Далі все в одній транзакції:
   1. `idempotency.claim()` виконує `INSERT … ON CONFLICT DO NOTHING`. Якщо ключ уже є, порівнюється `request_hash` — sha256 від нормалізованого тіла (`to_account_id` рядком, `amount` із двома знаками, тож `"5.0"` і `"5.00"` однакові). Інше тіло → 409 `idempotency_key_reused`. Те саме тіло → збережені статус і тіло відповіді без нового списання. Паралельний запит із тим самим ключем чекає на унікальному індексі, доки перший закомітить, і отримує його відповідь.
   2. Обидва рахунки блокуються одним запитом `SELECT … WHERE id = ANY($1) ORDER BY id FOR UPDATE`. Порядок за `id`, тож зустрічні перекази A→B і B→A не дають взаємоблокування. Отримувача немає → 404 `account_not_found`.
   3. `post_entry(відправник, −amount, type="transfer_out", counterparty=отримувач)` виконує умовний `UPDATE … WHERE balance_usd + $2 >= 0`; нестача → 409 `insufficient_funds`. Потім `post_entry(отримувач, +amount, type="transfer_in", counterparty=відправник)`. Обидва записи мають спільний `ref_id = transfer_id`.
   4. Результат — 201 або бізнес-помилка 404/409 — зберігається під ключем у тій самій транзакції. Неочікувана помилка (500) відкочує все, ключ звільняється, запит можна повторити.
3. Відповідь 201: `{"transfer_id", "from_account_id", "to_account_id", "amount"}`.

### 6.7 `POST /v1/purchases`

Тіло: `{"credits": 507}`.

1. `price = money.credits_price(credits)`.
2. Один `post_entry(−price, credits=+N, type="purchase", ref_id=purchase_id)`; нестача → 409 `insufficient_funds`.
3. Відповідь 201: `{"purchase_id", "credits", "price", "balance_usd", "balance_credits"}`.

### 6.8 `GET /v1/transactions`

- Параметри: `limit` від 1 до 100 (за замовчуванням 50), `cursor` — рядок із цілим `id` запису. Невалідний `limit` або `cursor` → 422.
- Keyset-пагінація: `WHERE account_id = $1 AND id < cursor ORDER BY id DESC LIMIT limit + 1`. Зайвий рядок лише показує, що є наступна сторінка; тоді `next_cursor` — `id` останнього запису на сторінці, інакше `null`. Користувач бачить лише власні записи.
- Відповідь 200: `{"items": [{"id", "type", "amount_usd", "credits", "ref_id", "counterparty_id", "created_at"}], "next_cursor": "123" | null}`.

### 6.9 Rate limit (бонус)

- Залежність `rate_limited_account_id` стоїть на трьох POST із API-ключем: deposits, transfers, purchases. Вебхук ключа не має й під ліміт не потрапляє.
- `INSERT INTO rate_limits … VALUES ($1, date_trunc('minute', now()), 1) ON CONFLICT DO UPDATE SET count = rate_limits.count + 1 RETURNING count`.
- `count > 30` → 429 `rate_limited` із заголовком `Retry-After` — секунди до кінця поточної хвилини (мінімум 1).
- `shortcut:` фіксоване вікно на стику хвилин пропускає до 60 запитів; ковзне вікно варто робити, лише якщо це стане важливим.

### 6.10 provider-mock

- Сховище — `dict` у пам'яті. Усі endpoint-и вимагають `Authorization: Bearer <PROVIDER_API_KEY>`, інакше 401. Помилки мок повертає у стандартному форматі FastAPI: єдиний формат ТЗ вимагає лише від wallet.
- `POST /invoices` → 201 `{"invoice_id": "inv_<hex>", "status": "pending", "pay_url": "<PUBLIC_URL>/pay/<invoice_id>"}`. `amount` — рядок-сума > 0, `currency` лише `"USD"`, інакше 422.
- `GET /invoices/{invoice_id}` → 200 `{"invoice_id", "status", "amount", "paid_amount", "currency", "external_id"}` або 404.
- `POST /invoices/{invoice_id}/simulate` з тілом `{"status", "paid_amount"?, "deliver_webhook": true, "duplicate": false}`:
  - фінальний інвойс → 409, невідомий → 404;
  - `paid_amount` за замовчуванням дорівнює сумі інвойсу для `paid` і `"0.00"` для `expired`; для `partially_paid` він обов'язковий;
  - вебхук надсилається синхронно всередині запиту. Тіло серіалізується один раз, підписується поточним часом, і при `duplicate: true` той самий запит іде двічі з тим самим `event_id`;
  - відповідь 200: `{"invoice": {...}, "deliveries": [{"status_code": 200}, ...]}`. Недоступний wallet дає `{"error": "<клас помилки>"}` замість падіння мока.

## 7. Помилки

Формат усюди, зокрема для відповідей, які генерує сам FastAPI:

```json
{"error": {"code": "insufficient_funds", "message": "Not enough USD balance", "details": {}}}
```

| HTTP | code |
|---|---|
| 400 | `invalid_json`, `invalid_idempotency_key` |
| 401 | `unauthorized`, `invalid_signature` |
| 404 | `not_found` (немає маршруту), `account_not_found`, `unknown_invoice` |
| 405 | `method_not_allowed` |
| 409 | `insufficient_funds`, `idempotency_key_reused` |
| 422 | `validation_error` (у `details` — поля з помилками), `same_account` |
| 429 | `rate_limited` |
| 502 / 503 / 504 | `provider_error` / `provider_unavailable` / `provider_timeout` |
| 500 | `internal_error` (подробиці лише в лог) |

Обробники: `ApiError`; `RequestValidationError` (тип `json_invalid` → 400, решта → 422); `StarletteHTTPException` (404 і 405 маршрутизації); `Exception` → 500.

## 8. Тестування

- Лише справжній PostgreSQL. URL береться з `TEST_DATABASE_URL`. `conftest` застосовує `schema.sql` і очищує таблиці перед кожним тестом. Локально це тимчасовий кластер PostgreSQL 17 у scratch-папці, у CI — service-контейнер `postgres:17`.
- Застосунок тестується через `httpx.AsyncClient(transport=ASGITransport(app))` усередині `app.router.lifespan_context(app)`. Провайдера підміняє `httpx.MockTransport`.
- TDD: кожен тест спершу падає з очікуваної причини, лише потім пишеться код.
- Обов'язкові з ТЗ:
  - таблиці комісії й ціни разом із нашими рядками з розділу 5;
  - підпис вебхука: правильний, неправильний, прострочений, із майбутнього, без заголовків;
  - повторний вебхук із тим самим `event_id`;
  - `Idempotency-Key` з тим самим і з іншим тілом;
  - недостатньо коштів.
- Понад ТЗ, бо це гроші:
  - 20 паралельних переказів по 10.00 з балансу 100.00: рівно 10 успішних, баланс 0.00;
  - зустрічні перекази A→B і B→A паралельно без взаємоблокування;
  - паралельні дублі вебхука дають одне зарахування;
  - вебхук після `expired` не зараховує гроші;
  - таймаут провайдера → 504, недоступність → 503;
  - формат помилок для неіснуючого маршруту й битого JSON;
  - у відповідях суми — рядки, а не float;
  - ключ у БД зберігається хешем;
  - звірка та rate limit (31-й POST → 429, вебхук не рахується);
  - мок надсилає підписаний вебхук і його дубль, фінальний інвойс не змінюється.

## 9. CI і Docker

- GitHub Actions, один job: service `postgres:17`, `uv sync --frozen`, `ruff check .`, `ruff format --check .`, `pytest`.
- Перед здачею запускаємо `docker compose up` на чистому клоні й проганяємо сценарій із README.

## 10. Документи

- `README.md` українською: запуск і тести (команди), схема «хто кого викликає», 3–7 ключових рішень і компромісів, що спрощено або не зроблено.
- `AI_NOTES.md` українською, до однієї сторінки. Збирається наприкінці з фактів: кожне виправлення помилки AI йде окремим комітом із рядком `AI_NOTES: <що було не так>` у тілі повідомлення, тож `git log --grep "AI_NOTES:"` дає готовий журнал.
- Ця специфікація та план лежать у `docs/superpowers/`.

## 11. Правила коду

- **TDD (Superpowers):** спершу тест, бачимо, що він падає, потім мінімальний код.
- **ponytail, рівень full:** найменша повна зміна; жодних абстракцій, опцій і конфігурації «на потім»; коментар пояснює лише «чому»; відоме обмеження позначається `shortcut: <межа>, <коли покращити>`.
- **code-quality:** один файл — одна відповідальність; потік читається джуном без пояснень; однотипні endpoint-и мають однаковий ритм (розділ 6).
- **Перед словом «готово»:** свіжий прогін `pytest` і `ruff` у тому самому повідомленні (verification-before-completion).
- **Рев'ю:** після кожної великої задачі агенти PR Review Toolkit, наприкінці — повне рев'ю.

## 12. Відомі компроміси й ризики

- Вебхук може прийти раніше, ніж wallet запише депозит (між відповіддю провайдера й `INSERT`). Тоді wallet поверне 404 `unknown_invoice`, і справжній провайдер повторить вебхук. У моку вебхук можливий лише після `simulate`, а `invoice_id` клієнт отримує вже після запису, тож тут цей випадок не виникає.
- Якщо провайдер створив інвойс, а `INSERT` депозиту впав, у провайдера лишається інвойс-сирота. Клієнт не отримав `pay_url`, тож оплатити його не може.
- `schema.sql` застосовується під час кожного старту wallet. Це безпечно, поки процес один (`shortcut:`); для кількох процесів — Alembic або advisory lock.
- Ключі ідемпотентності та вікна rate limit не прибираються (`shortcut:`).
- Rate limit на фіксованому вікні (`shortcut:`, див. 6.9).
