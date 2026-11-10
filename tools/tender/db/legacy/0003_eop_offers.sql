-- Offers of each procedure (who offered what), read from the public procedure pages of ЦАИС ЕОП
-- (service.eop.bg, the answers app.eop.bg/today/{id} shows). Persistent like tr.*: the 'offers' lane
-- fills it, the build copies it into live.offer (db/derive/60_offers.sql).
CREATE SCHEMA IF NOT EXISTS eopsvc;

CREATE TABLE eopsvc.queue (
  tender_id bigint PRIMARY KEY,
  reason text NOT NULL,                 -- backfill | update | recheck
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'error')),
  attempts int NOT NULL DEFAULT 0,
  next_at timestamptz NOT NULL DEFAULT now(),
  fetched_at timestamptz,
  sha256 text,                          -- of the raw answer kept in data/raw/eop_svc
  last_error text
);
CREATE INDEX ON eopsvc.queue (status, next_at);

CREATE TABLE eopsvc.offer (
  tender_id bigint NOT NULL,
  lot_no int NOT NULL,                  -- 0: the procedure has no lots (or the "general" pseudo-lot)
  round int NOT NULL,
  offer_id bigint NOT NULL,
  bidder_name text,
  bidder_eik text,                      -- only a valid 9/13-digit ЕИК; anything else (a personal number) is dropped
  consortium jsonb,                     -- [{name, eik}] members of a joint offer
  submitted_at timestamptz,
  price numeric,                        -- in the currency of the procedure, only once prices are opened
  price_opened boolean NOT NULL,
  fetched_at timestamptz NOT NULL,
  PRIMARY KEY (tender_id, lot_no, round, offer_id)
);
CREATE INDEX ON eopsvc.offer (bidder_eik);

-- the daily validity check (eop-check): canary procedures, answer shape, completeness against offersCount
CREATE TABLE eopsvc.check_run (
  at timestamptz PRIMARY KEY DEFAULT now(),
  ok boolean NOT NULL,
  report jsonb NOT NULL
);
