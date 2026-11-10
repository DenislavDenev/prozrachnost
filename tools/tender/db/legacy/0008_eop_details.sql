-- What the procedure page of ЦАИС ЕОП shows beyond offers and messages (audit 27.09.2026,
-- docs/audit-2026-09-27.md), from GetPublishedTenderDetails, read with the offers.
-- Rows are never deleted on one answer: what a later answer no longer lists gets gone_at and a line in
-- ops.change_log, and stays (methodology 9).

-- the participant of an offer is what ЦАИС ЕОП shows (OfferName); the one who pressed "submit" is kept
-- apart and only when it is a company (a valid ЕИК): a person who submitted is never stored
ALTER TABLE eopsvc.offer ADD COLUMN submitter_name text, ADD COLUMN submitter_eik text;

ALTER TABLE eopsvc.announcement ADD COLUMN gone_at timestamptz;

-- dates and flags of the procedure as ЦАИС ЕОП holds them
CREATE TABLE eopsvc.detail (
  tender_id bigint PRIMARY KEY,
  offers_from timestamptz,              -- OfferPhaseStartDate
  offers_until timestamptz,             -- OfferPhaseEndDate: the deadline in force (after any extension)
  opening_at timestamptz,               -- OpeningOfOffersDate
  prices_opening_at timestamptz,        -- OpeningOfPricesDate
  parent_tender_id bigint,              -- ParentTender (a call under a framework agreement or a DPS)
  linked jsonb,                         -- LinkedTenders / ChildTenders: [{tender_id, unp, name}]
  fetched_at timestamptz NOT NULL
);

-- every notice and decision ЦАИС ЕОП published for the procedure ("Обявления и решения"); the full text
-- stays in the raw answer (data/raw/eop_svc), the page links to ЦАИС ЕОП and to the OJ EU
CREATE TABLE eopsvc.publication (
  tender_id bigint NOT NULL,
  id bigint NOT NULL,                   -- TenderPublicationId, the F-number on the page (= noticeId of the open data)
  form_type int,                        -- PublicationFormType (named through the open data, derive 65_eop.sql)
  ted_number text,                      -- OJ EU notice number, e.g. 593377-2026
  sent_at timestamptz,
  published_at timestamptz,
  gone_at timestamptz,
  PRIMARY KEY (tender_id, id)
);

-- appeals before the Commission for Protection of Competition ("Производства")
CREATE TABLE eopsvc.appeal (
  tender_id bigint NOT NULL,
  register_id text NOT NULL,            -- RegisterId, e.g. ВХР-1614-19.05.2026
  proceedings_number text,              -- e.g. КЗК/522/2026
  kind text,                            -- ProceedingsType
  subjects text[],                      -- ProceedingsSubsections, e.g. {"ЗОП - чл 45"}
  initiators text[],                    -- who appealed (companies; a person's name is not kept)
  defendants text[],
  interim_measures boolean,             -- asked for (InterimMeasures)
  imposed_measures int,                 -- ImposedInterimMeasures, count
  imposed_penalties int,
  status text,                          -- CurrentStatus
  filed_on date,                        -- DossierPublishDate
  started_on date,                      -- ProceedingsStartDate
  last_decision_on date,
  closed_on date,
  link text,                            -- reg.cpc.bg dossier
  gone_at timestamptz,
  PRIMARY KEY (tender_id, register_id)
);

-- the attached documents of the procedure: name, size and time only (no author, no file)
CREATE TABLE eopsvc.document (
  tender_id bigint NOT NULL,
  id bigint NOT NULL,
  name text,
  size bigint,
  created_at timestamptz,
  gone_at timestamptz,
  PRIMARY KEY (tender_id, id)
);

-- the daily completeness check against ЦАИС ЕОП, per procedure read that day (eop_offers.check)
ALTER TABLE eopsvc.check_run ADD COLUMN IF NOT EXISTS mismatches jsonb;
