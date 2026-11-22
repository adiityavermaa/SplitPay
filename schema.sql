-- SplitPay mock database schema
-- Initialize with: sqlite3 splitpay.db < schema.sql

CREATE TABLE IF NOT EXISTS users (
  id          TEXT PRIMARY KEY,
  phone       TEXT UNIQUE NOT NULL,
  upi_id      TEXT UNIQUE NOT NULL,
  balance     REAL NOT NULL DEFAULT 50000.00
);

CREATE TABLE IF NOT EXISTS merchants (
  pa          TEXT PRIMARY KEY,
  pn          TEXT NOT NULL,
  mc          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
  parent_id    TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL,
  merchant_pa  TEXT NOT NULL,
  total_amount REAL NOT NULL,
  status       TEXT NOT NULL,
  created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
  txn_id     TEXT PRIMARY KEY,
  parent_id  TEXT NOT NULL,
  amount     REAL NOT NULL,
  mdr        REAL NOT NULL DEFAULT 0,
  status     TEXT NOT NULL,
  created_at TEXT NOT NULL
);

INSERT OR IGNORE INTO users (id, phone, upi_id, balance)
VALUES ('U001', '9876543210', 'demo.user@mockupi', 50000.00);

INSERT OR IGNORE INTO merchants (pa, pn, mc) VALUES
  ('cafe.mocha@mockupi',    'Cafe Mocha',       '5812'),
  ('bookstore@mockupi',     'Campus Bookstore', '5942'),
  ('metro.canteen@mockupi', 'Metro Canteen',    '5814'),
  ('stationery@mockupi',    'Stationery Mart',  '5112'),
  ('print.shop@mockupi',    'Print & Copy',     '7338');