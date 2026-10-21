# LedgerLite

Тестове завдання «AI Software Engineer (Python)»: два сервіси, які піднімає одна команда.

- **wallet** (`:8000`) — рахунки користувачів у USD і кредитах: депозити через провайдера, перекази, покупка кредитів, історія операцій.
- **provider** (`:8001`) — імітація платіжного провайдера: інвойси в пам'яті та підписані вебхуки.

## Запуск

Потрібні Docker з Compose v2 (для тестів ще й [uv](https://docs.astral.sh/uv/)).

```bash
docker compose up --build
```

Документація API: <http://localhost:8000/docs> (wallet) і <http://localhost:8001/docs> (провайдер). У `docker-compose.yml` змінні оточення мають dev-значення; щоб їх змінити, скопіюйте `.env.example` у `.env` (тоді підставте свій `PROVIDER_API_KEY` у виклик `simulate` нижче).

Контейнери wallet і provider не мають healthcheck, тож перед сценарієм дочекайтеся, поки `curl -s localhost:8000/v1/accounts/me` відповість JSON-помилкою 401.

## Сценарій перевірки

```bash
docker compose exec wallet python -m wallet.cli create-account alice   # друкує account_id і api_key
docker compose exec wallet python -m wallet.cli create-account bob
KEY=<api_key alice>
BOB=<account_id bob>

# депозит: wallet створює інвойс у провайдера
curl -s -X POST localhost:8000/v1/deposits -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"amount": "10.00"}'
# 201: {"deposit_id":"…","status":"pending","amount":"10.00","pay_url":"http://localhost:8001/pay/inv_…"}
INV=<останній сегмент pay_url, тобто inv_…>

# оплата з дублем вебхука: провайдер надсилає той самий вебхук двічі
curl -s -X POST localhost:8001/invoices/$INV/simulate \
  -H "Authorization: Bearer dev-provider-key" -H "Content-Type: application/json" \
  -d '{"status": "paid", "duplicate": true}'
# "deliveries":[{"status_code":200},{"status_code":200}]

curl -s localhost:8000/v1/accounts/me -H "X-API-Key: $KEY"
# "balance_usd":"9.85", "balance_credits":0: дубль не зарахувався, комісія 0.15 утримана

curl -s -X POST localhost:8000/v1/transfers -H "X-API-Key: $KEY" -H "Idempotency-Key: t-1" \
  -H "Content-Type: application/json" -d "{\"to_account_id\": \"$BOB\", \"amount\": \"5.00\"}"
# 201; той самий запит ще раз повертає ту саму відповідь і нічого не списує

curl -s -X POST localhost:8000/v1/purchases -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"credits": 30}'
# "price":"3.00", "balance_usd":"1.85", "balance_credits":30

curl -s "localhost:8000/v1/transactions?limit=10" -H "X-API-Key: $KEY"
# три записи, нові першими: purchase -3.00, transfer_out -5.00, deposit 9.85; "next_cursor":null

# звірка депозитів, вебхук яких загубився
docker compose exec wallet python -m wallet.cli reconcile --older-than 15
# resolved=0 pending=0 failed=0: свіжих pending-депозитів старше 15 хвилин немає
```

Щоб побачити звірку в дії, створіть ще один депозит, оплатіть його без вебхука (`"deliver_webhook": false` у `simulate`) і запустіть `reconcile --older-than 0`: вона виведе `resolved=1 pending=0 failed=0`, а баланс зросте на суму за мінусом комісії. Повторний запуск нічого не змінить.

## Тести й лінтер

```bash
docker compose up -d db
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Тести працюють зі справжнім PostgreSQL: за замовчуванням `postgresql://ledger:ledger@localhost:55432/ledger_test`, змінна оточення `TEST_DATABASE_URL` це змінює. Базу `ledger_test` створює `docker/create-test-db.sql` під час першого старту контейнера. Фікстури роблять `TRUNCATE` усіх таблиць, а compose публікує на тому ж порту й робочу базу `ledger`, тому `tests/conftest.py` відмовляється працювати з базою, назва якої не закінчується на `_test`.

Зараз у наборі 163 тести: гроші (таблиці з ТЗ), підпис вебхука (правильний, неправильний, прострочений), дубль вебхука, ідемпотентність переказів і паралельні перекази, недостатньо коштів, звірка, rate limit, а також наскрізний тест «wallet ↔ мок провайдера».

CI — GitHub Actions (`.github/workflows/ci.yml`): `ruff check`, `ruff format --check` і `pytest` проти сервісу `postgres:17`.

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
2. **Вебхук.** Підпис перевіряється по сирих байтах тіла до парсингу, через `hmac.compare_digest`. Свіжість перевіряється за `X-Timestamp` (допуск 5 хвилин), а не за `occurred_at`: запізнілий вебхук законний. Дубль і пізній вебхук зупиняє перехід `pending → фінальний` під `FOR UPDATE`, тож окрема таблиця подій не потрібна, а `event_id` лишається в `closed_by_event_id`. На дубль відповідаємо 200, щоб провайдер не повторював його.
3. **Ідемпотентність переказів.** Ключ діє в межах користувача й захоплюється `INSERT … ON CONFLICT` у тій самій транзакції, що й переказ. Рахунки блокуються в порядку `id` до захоплення ключа, а не після: зовнішній ключ запису ключа бере розділюваний замок на рядок відправника, і порядок «спочатку ключ, потім рахунки» призводив до взаємоблокування паралельних переказів від одного відправника. Паралельний повтор чекає на блокуванні рахунків (`FOR UPDATE`) і отримує збережену відповідь. Зберігаються точні байти відповіді (TEXT, не JSONB, бо JSONB переставляє ключі). Бізнес-помилки 404/409 теж зберігаються, 500 — ні.
4. **Без взаємоблокувань.** Обидва рахунки переказу блокуються одним запитом із `ORDER BY id`, тож зустрічні перекази (A→B і B→A) не чекають один на одного по колу.
5. **Провайдер.** Виклик іде поза транзакцією БД, з таймаутами 2 с на з'єднання і 5 с на відповідь. Таймаут → 504, недоступність → 503, погана відповідь → 502, кожен зі своїм `code`. Депозит записується лише після відповіді провайдера, ретраїв немає, бо `POST /invoices` не ідемпотентний.
6. **API-ключі.** У БД лежить sha256 від випадкового 256-бітного ключа. Bcrypt тут не потрібен і не дав би знайти рахунок за ключем одним запитом.
7. **Бонуси без нової інфраструктури.** Rate limit — ковзне вікно в Postgres: кожен прийнятий POST записується в `rate_limit_hits`, і за будь-які 60 секунд ключ проходить не більше 30 разів; перевірку й запис одного ключа серіалізує advisory lock, вебхук не рахується. Звірка — CLI-команда, яка викликає ту саму функцію переходу статусу, що й вебхук.

## Що спрощено або не зроблено

- Міграцій немає: `schema.sql` застосовується під час кожного старту й безпечний лише для одного процесу wallet. База в compose без постійного тому, тож `docker compose down` стирає дані.
- Ключі ідемпотентності ніколи не видаляються.
- Покупки й депозити без `Idempotency-Key`: ТЗ вимагає його лише для переказів.
- Тіло запиту розбирається до автентифікації: некоректний JSON дає 400 `invalid_json` навіть без API-ключа, і такі запити не рахуються в rate limit.
- Усі запити з тілом потребують `Content-Type: application/json`, інакше FastAPI відповідає 422 (у прикладах вище заголовок скрізь є).
- Тіло вебхука читається без обмеження розміру: за allow-list провайдера це прийнятно, для публічного endpoint-а розмір треба обмежити.
- Вебхук, що прийшов раніше, ніж wallet записав депозит, отримує 404; справжній провайдер повторив би його, мок — ні.
- Якщо провайдер створив інвойс, а запис депозиту впав, інвойс лишається сиротою (клієнт не отримав `pay_url`).
- Звірка запускається вручну, розклад (cron) не налаштований. Вона зараховує депозит до `resolved`, навіть якщо вебхук закрив його за мить до того; гроші в безпеці, бо рядок блокується й статус перевіряється так само.
- Мок повертає помилки у стандартному форматі FastAPI, а не в єдиному форматі wallet. Його `pay_url` лише називає інвойс: сторінки оплати немає, оплату імітує `simulate`.
- Контейнери wallet і provider без healthcheck, тому `docker compose up --wait` означає «запущено», а не «слухає».
- Наслідок правил ціни: 500 кредитів коштують менше за 499, а 1000 — менше за 999.

## Як ми працювали

Роботу вели за AI TDD: для кожної задачі спершу окремим комітом `test: … (red)` додавали тести, які мають падати, а код приходив наступним комітом `feat:` без жодної правки тестів. Після кожної задачі керівний агент перезапускав набір, порівнював файли тестів побайтово й мутував критичну логіку (гроші, підпис, вебхук, перекази, rate limit), щоб переконатися, що тести це ловлять. Виняток — правила грошей: вони писалися до цієї вимоги, без окремого red-коміту, і перевірені мутаціями постфактум. Історію не сквошено: дивіться `git log`; дизайн лежить у `docs/superpowers/specs/`, план — у `docs/superpowers/plans/`, а як використовували AI і де він помилявся — в [AI_NOTES.md](AI_NOTES.md).
