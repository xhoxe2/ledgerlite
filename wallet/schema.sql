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

CREATE TABLE IF NOT EXISTS rate_limit_hits (
    account_id UUID NOT NULL REFERENCES accounts (id),
    at         TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS rate_limit_hits_account_id_at ON rate_limit_hits (account_id, at);
