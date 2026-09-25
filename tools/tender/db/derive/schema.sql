-- Schema `stage`: rebuilt from the raw sources on every build, then swapped in as `live`.
DROP SCHEMA IF EXISTS stage CASCADE;
CREATE SCHEMA stage;
SET search_path = stage;

CREATE TABLE buyer (
  eik text PRIMARY KEY, eik_valid boolean NOT NULL, name text, type text, main_activity text
);

CREATE TABLE tender (
  unp text PRIMARY KEY, tender_id text, buyer_eik text, subject text, procedure_type text,
  cpv text, cpv_description text, contract_type text, estimated_value numeric, currency text,
  is_eu_funded boolean, european_program text, lots_count int, submission_deadline timestamp,
  published_at timestamp, notice_type text, is_cancelled boolean, execution_nuts text,
  source_day date, synthetic boolean NOT NULL
);

CREATE TABLE lot (
  unp text, lot_no int, title text, estimated_value numeric, currency text, cpv text,
  PRIMARY KEY (unp, lot_no)
);

CREATE TABLE contract (
  id text PRIMARY KEY, source text NOT NULL, source_day date NOT NULL, notice_id text,
  unp text, contract_number text, lot_no int, buyer_eik text, supplier_display text,
  awarded_to_group boolean, subject text, tender_name text, procedure_type text, cpv text,
  cpv_description text, contract_type text, contract_date date, published_at timestamp,
  effective_date date, date_basis text,
  value_initial numeric, value_current numeric, currency text, fx_rate numeric,
  value_initial_eur numeric, value_current_eur numeric, amount_eur numeric,
  value_flag text NOT NULL, date_flag text NOT NULL,
  estimated_value numeric, estimate_currency text, estimated_eur numeric,
  offers_count int, sme_offers_count int, disqualified_offers_count int,
  is_eu_funded boolean, european_program text, is_framework boolean NOT NULL,
  framework_contract text, linked_tenders text, direct_award_justification text,
  award_method text, legal_basis text, has_subcontractors boolean, supplier_nuts text,
  supplier_size text, contract_period_days int, annex_count int NOT NULL
);

CREATE TABLE contract_supplier (
  contract_id text NOT NULL, position int NOT NULL, party_key text NOT NULL, eik text,
  name text, joint boolean NOT NULL, PRIMARY KEY (contract_id, position)
);

CREATE TABLE amendment (
  contract_id text NOT NULL, published_at timestamp, source_day date, last_value numeric,
  current_value numeric, difference numeric, currency text, reason text, description text
);

CREATE TABLE subcontract (
  contract_id text NOT NULL, party_key text NOT NULL, eik text, name text, percent numeric,
  amount numeric
);
