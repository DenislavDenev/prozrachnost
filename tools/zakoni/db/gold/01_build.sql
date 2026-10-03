-- gold from silver: one transaction, run by ingest/gold.py with %(build)s the new build id.
-- The 30-day rule (Закон за нормативните актове, чл. 26, ал. 4, в сила от 04.11.2016): the period for proposals is not
-- shorter than 30 days; in exceptional cases, with the reasons stated in the motives, not shorter than 14 days.
-- The days are close minus open as the portal gives the dates; the rule applies to consultations opened on or after 2016-11-04
-- and to the acts the article names (a draft normative act); the others (a non-normative act, e.g. a decision or a strategy)
-- are shown with their days but without the indicator.
TRUNCATE gold.act_institution, gold.act_relation, gold.act, gold.consultation, gold.strategy_doc, gold.impact_contract CASCADE;

INSERT INTO gold.act (pris_id, doc_num, accepted, year, act_type, about, about_original, legal_reason, importer, protocol,
                      consultation_reg_num, gazette_number, gazette_year, active, confidential, origin, tags, relations,
                      source, source_url, raw_sha256, first_seen, last_seen)
SELECT a.pris_id, a.doc_num, a.accepted, extract(year FROM a.accepted)::smallint, a.legal_act_type, a.about, a.about_raw,
       a.legal_reason, a.importer, a.protocol,
       (SELECT c.reg_num FROM silver.consultation c WHERE c.valid_to IS NULL AND c.reg_num = a.public_consultation_number),
       CASE WHEN abs(a.gazette_year - extract(year FROM a.accepted)) <= 1 THEN a.gazette_number END,
       CASE WHEN abs(a.gazette_year - extract(year FROM a.accepted)) <= 1 THEN a.gazette_year END, a.active, a.confidential, a.origin,
       (SELECT array_agg(t.tag ORDER BY t.ord) FROM silver.pris_tag t WHERE t.pris_id = a.pris_id AND t.valid_to IS NULL),
       (SELECT count(*) FROM silver.pris_related r WHERE r.pris_id = a.pris_id AND r.valid_to IS NULL),
       'data.egov.bg, АМС', 'https://data.egov.bg/data/view/18da0fff-79b2-45c6-a9af-1509df96261b', a.raw_sha256,
       (SELECT min(x.valid_from)::date FROM silver.pris_act x WHERE x.pris_id = a.pris_id), a.valid_from::date
FROM silver.pris_act a WHERE a.valid_to IS NULL;

INSERT INTO gold.act_institution
SELECT DISTINCT i.pris_id, i.institution_id, i.name FROM silver.pris_institution i JOIN gold.act a USING (pris_id) WHERE i.valid_to IS NULL;

INSERT INTO gold.act_relation
SELECT r.pris_id, r.ord, r.relation_type, r.related_pris_id, EXISTS (SELECT 1 FROM gold.act x WHERE x.pris_id = r.related_pris_id),
       r.act_type, r.act_name
FROM silver.pris_related r JOIN gold.act a USING (pris_id) WHERE r.valid_to IS NULL AND r.related_pris_id <> r.pris_id;

INSERT INTO gold.consultation
SELECT c.reg_num, split_part(c.reg_num, '-', 1)::int, c.name, c.description, c.consultation_type, c.act_type, c.date_open,
       c.date_close, c.date_close - c.date_open,
       (c.date_close - c.date_open) < 30,
       c.date_open >= DATE '2016-11-04' AND coalesce(c.act_type, '') NOT LIKE 'Ненормативен%%' AND (c.date_close - c.date_open) < 30,
       CASE WHEN btrim(c.short_term_reason, ' .-') = '' THEN NULL ELSE c.short_term_reason END,
       btrim(coalesce(c.short_term_reason, ''), ' .-') <> '',
       c.policy_area, c.policy_area LIKE 'Архив - %%',
       c.institution_id, c.institution_name,
       (SELECT m.municipality_id FROM ref.institution_municipality m WHERE m.institution_id = c.institution_id),
       c.law_name, c.law_id,
       (SELECT a.pris_id FROM gold.act a WHERE a.pris_id = c.pris_id),
       c.comment_count, (SELECT count(*) FROM silver.consultation_file f WHERE f.reg_num = c.reg_num AND f.valid_to IS NULL),
       'data.egov.bg, АМС', 'https://strategy.bg/bg/public-consultations/' || split_part(c.reg_num, '-', 1), c.raw_sha256,
       (SELECT min(x.valid_from)::date FROM silver.consultation x WHERE x.reg_num = c.reg_num), c.valid_from::date
FROM silver.consultation c WHERE c.valid_to IS NULL;

INSERT INTO gold.strategy_doc
SELECT s.doc_key, s.name, s.level, s.policy_area, s.doc_type, s.accepting_institution_type,
       (SELECT a.pris_id FROM gold.act a WHERE a.pris_id = s.pris_act_id),
       (SELECT c.reg_num FROM gold.consultation c WHERE c.reg_num = s.public_consultation_number),
       s.date_accepted, s.date_expiring, s.active,
       (SELECT array_agg(u.institution ORDER BY u.ord) FROM silver.strategy_author u WHERE u.doc_key = s.doc_key AND u.valid_to IS NULL),
       (SELECT count(*) FROM silver.strategy_file f WHERE f.doc_key = s.doc_key AND f.valid_to IS NULL),
       'data.egov.bg, АМС', 'https://data.egov.bg/data/view/18da0fff-79b2-45c6-a9af-1509df96261b', s.raw_sha256,
       (SELECT min(x.valid_from)::date FROM silver.strategy_doc x WHERE x.doc_key = s.doc_key), s.valid_from::date
FROM silver.strategy_doc s WHERE s.valid_to IS NULL;

INSERT INTO gold.impact_contract
SELECT i.ic_key, i.institution_id, i.institution_name, i.contract_date, i.price_bgn, round(i.price_bgn / 1.95583, 2), i.eik,
       i.executor, i.executor_kind, i.subject, i.description, 'data.egov.bg, АМС',
       'https://data.egov.bg/data/view/18da0fff-79b2-45c6-a9af-1509df96261b', i.raw_sha256
FROM silver.impact_contract i WHERE i.valid_to IS NULL;

-- keys that point nowhere: the row stays with an empty key, the reason is kept
INSERT INTO gold.unmatched (build_id, tbl, key, field, value, reason)
SELECT %(build)s, 'act', a.pris_id::text, 'public_consultation_number', s.public_consultation_number, 'консултацията я няма в справката'
FROM silver.pris_act s JOIN gold.act a USING (pris_id) WHERE s.valid_to IS NULL AND s.public_consultation_number IS NOT NULL AND a.consultation_reg_num IS NULL;
INSERT INTO gold.unmatched (build_id, tbl, key, field, value, reason)
SELECT %(build)s, 'consultation', c.reg_num, 'pris_id', s.pris_id::text, 'актът го няма в ПРИС'
FROM silver.consultation s JOIN gold.consultation c USING (reg_num) WHERE s.valid_to IS NULL AND s.pris_id IS NOT NULL AND c.act_pris_id IS NULL;
INSERT INTO gold.unmatched (build_id, tbl, key, field, value, reason)
SELECT %(build)s, 'consultation', c.reg_num, 'municipality_id', c.institution_name, 'общинска институция без община в справочника'
FROM gold.consultation c WHERE c.level = 'Общинско' AND c.municipality_id IS NULL AND c.institution_name IS NOT NULL;
INSERT INTO gold.unmatched (build_id, tbl, key, field, value, reason)
SELECT %(build)s, 'consultation', c.reg_num, 'institution_name', NULL, 'справката не дава име на институцията (institution_id ' || c.institution_id || ')'
FROM gold.consultation c WHERE c.institution_name IS NULL;
INSERT INTO gold.unmatched (build_id, tbl, key, field, value, reason)
SELECT %(build)s, 'strategy_doc', s.doc_key, 'pris_act_id', x.pris_act_id::text, 'актът го няма в ПРИС'
FROM silver.strategy_doc x JOIN gold.strategy_doc s USING (doc_key) WHERE x.valid_to IS NULL AND x.pris_act_id IS NOT NULL AND s.act_pris_id IS NULL;
INSERT INTO gold.unmatched (build_id, tbl, key, field, value, reason)
SELECT %(build)s, 'impact_contract', i.ic_key, 'eik', NULL, 'няма ЕИК (физическо лице или липсва)' FROM gold.impact_contract i WHERE i.eik IS NULL;
INSERT INTO gold.unmatched (build_id, tbl, key, field, value, reason)
SELECT %(build)s, 'act', a.pris_id::text, 'gazette', coalesce(s.gazette_number::text, '') || ' / ' || coalesce(s.gazette_year_raw, ''),
       'годината на ДВ не се определя или не е около годината на акта (' || extract(year FROM a.accepted)::int || '): броят и годината не се показват'
FROM silver.pris_act s JOIN gold.act a USING (pris_id) WHERE s.valid_to IS NULL AND (s.gazette_number IS NOT NULL OR s.gazette_year_raw IS NOT NULL) AND a.gazette_year IS NULL;
INSERT INTO gold.unmatched (build_id, tbl, key, field, value, reason)
SELECT %(build)s, 'act_relation', r.pris_id::text, 'related_pris_id', r.related_pris_id::text, 'актът е посочен като свързан със себе си: връзката не се показва'
FROM silver.pris_related r JOIN gold.act a USING (pris_id) WHERE r.valid_to IS NULL AND r.related_pris_id = r.pris_id;
