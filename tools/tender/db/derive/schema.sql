-- Schema `stage`: rebuilt from the raw sources on every build, then swapped in as `live`.
DROP SCHEMA IF EXISTS stage CASCADE;
CREATE SCHEMA stage;
SET search_path = stage;

-- Names as the sources write them sometimes carry an identity number („…, ЕГН 1234567890“, „ЕИК …“)
-- or the representative of a company holder („X ООД, представлявано от Y ЕГН …“). Shown names carry
-- neither: no identity number of any kind, and the holder is the company, not its representative.
-- A person's name has no digits at all; a company keeps short numbers that are part of its
-- registered name („Дива - 90“). ingest/normalize.py clean_name() is the same rule.
CREATE FUNCTION clean_name(t text) RETURNS text LANGUAGE sql IMMUTABLE AS $f$
  SELECT nullif(btrim(regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(t,
    '[,;]?\s*([Пп]редставлява[нщ][а-я]*|[Чч]рез|[Сс]\s+представляващ)\s.*$', ''),
    '([Ее][Гг][Нн]|[Лл][Нн][Чч]|[Ее][Ии][Кк]|[Бб][Уу][Лл][Сс][Тт][Аа][Тт])[\s:№.]*[0-9]+', '', 'g'),
    '[0-9]{6,}', '', 'g'),
    '[,;]?\s*([Ее][Гг][Нн]|[Лл][Нн][Чч])\s*[:№.]*\s*$', ''),
    '\s{2,}', ' ', 'g'), ' ,;-–—'), '')
$f$;
CREATE FUNCTION clean_person(t text) RETURNS text LANGUAGE sql IMMUTABLE AS $f$
  SELECT nullif(btrim(regexp_replace(regexp_replace(clean_name(t), '[0-9]', '', 'g'), '\s{2,}', ' ', 'g'), ' ,;-–—'), '')
$f$;

CREATE TABLE buyer (
  eik text PRIMARY KEY, eik_valid boolean NOT NULL, name text, type text, main_activity text,
  locality text, postal_code text, nuts text  -- address from OCDS parties, when published
);

CREATE TABLE tender (
  unp text PRIMARY KEY, tender_id text, buyer_eik text, subject text, procedure_type text,
  cpv text, cpv_description text, contract_type text, estimated_value numeric, currency text,
  estimated_eur numeric, is_eu_funded boolean, european_program text, lots_count int, submission_deadline timestamp,
  published_at timestamp, notice_type text, is_cancelled boolean, execution_nuts text,
  source_day date, synthetic boolean NOT NULL
);

CREATE TABLE lot (
  unp text, lot_no int, title text, estimated_value numeric, currency text, cpv text, estimated_eur numeric,
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
  estimated_value numeric, estimate_currency text, estimated_eur numeric, estimate_ratio numeric,
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

-- raw record pointers: every published version of a tender, contract or OCDS release
CREATE TABLE source_record (entity text NOT NULL, ref text NOT NULL, day text NOT NULL, kind text NOT NULL, idx int NOT NULL);
