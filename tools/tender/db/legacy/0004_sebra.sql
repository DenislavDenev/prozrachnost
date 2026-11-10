-- Budget payments from СЕБРА (the quarterly lists of individual payments >= 5 000 лв. under
-- ПМС 299/2016, чл. 12, published by МЕУ on data.egov.bg). Persistent: ingest/sebra.py loads each
-- published file once (again when it changes); the build derives live.payment (db/derive/70_payments.sql).
-- Payments to natural persons arrive anonymised; for them only amount, date, payer and code are kept.
CREATE SCHEMA IF NOT EXISTS sebra;

CREATE TABLE sebra.resource (
  uri text PRIMARY KEY,
  name text NOT NULL,                 -- "За периода 01.04.2026 г. - 30.06.2026 г."
  format text NOT NULL,               -- csv | zip
  updated_at timestamptz,             -- as the portal reports it; a change means reload
  sha256 text,
  rows int,
  loaded_at timestamptz
);

CREATE TABLE sebra.payment (
  resource_uri text NOT NULL REFERENCES sebra.resource(uri),
  row_no int NOT NULL,
  settlement_date date,
  receiver_name text,                 -- 'ФИЗИЧЕСКО ЛИЦЕ' for anonymised persons
  is_person boolean NOT NULL,
  receiver_iban text,                 -- companies and institutions only
  fin_code text, fin_name text,       -- the payer's code and name in СЕБРА
  amount numeric, currency text,
  reason text,                        -- REASON1 / REASON2, companies and institutions only
  reg_date date, reg_no text,
  pay_code text,                      -- СЕБРА payment code (10-90)
  organization text, primary_organization text, primary_org_code text,
  PRIMARY KEY (resource_uri, row_no)
);
CREATE INDEX ON sebra.payment (settlement_date);
