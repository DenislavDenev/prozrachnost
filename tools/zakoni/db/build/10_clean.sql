-- The clean tables from the current loads of the landing zone, whole, in one transaction (ingest/build.py runs each
-- statement in order). Nothing here is typed by hand: the only manual list is db/ref/institution_municipality.csv, which
-- is loaded as a source of its own.
--
-- The 30-day rule (Закон за нормативните актове, чл. 26, ал. 4, в сила от 04.11.2016): the period for proposals is not
-- shorter than 30 days; in exceptional cases, with the reasons stated in the motives, not shorter than 14 days.
-- The days are close minus open as the portal gives the dates; the rule applies to consultations opened on or after 2016-11-04
-- and to the acts the article names (a draft normative act); the others (a non-normative act, e.g. a decision or a strategy)
-- are shown with their days but without the indicator.
-- A day (first_seen, last_seen) is the day in Bulgaria of the moment the archive read the source.
CREATE TEMP TABLE zk_current ON COMMIT DROP AS SELECT load_id FROM ops.load WHERE tool = 'zakoni' AND is_current;

TRUNCATE zakoni.act_institution, zakoni.act_relation, zakoni.act, zakoni.consultation_file, zakoni.consultation,
         zakoni.strategy_doc, zakoni.impact_contract, zakoni.unmatched;

CREATE TEMP TABLE zk_act ON COMMIT DROP AS SELECT * FROM zakoni_src.pris_act WHERE load_id IN (SELECT load_id FROM zk_current);
CREATE TEMP TABLE zk_cons ON COMMIT DROP AS SELECT * FROM zakoni_src.consultation WHERE load_id IN (SELECT load_id FROM zk_current);
CREATE TEMP TABLE zk_related ON COMMIT DROP AS SELECT * FROM zakoni_src.pris_related WHERE load_id IN (SELECT load_id FROM zk_current);
CREATE TEMP TABLE zk_strategy ON COMMIT DROP AS SELECT * FROM zakoni_src.strategy_doc WHERE load_id IN (SELECT load_id FROM zk_current);
ANALYZE zk_act;
ANALYZE zk_cons;

INSERT INTO zakoni.act (pris_id, doc_num, accepted, year, act_type, about, about_original, legal_reason, importer, protocol,
                        consultation_reg_num, gazette_number, gazette_year, active, confidential, origin, tags, relations,
                        source, source_url, raw_sha256, first_seen, last_seen)
SELECT a.pris_id, a.doc_num, a.accepted, extract(year FROM a.accepted)::smallint, a.legal_act_type, a.about, a.about_raw,
       a.legal_reason, a.importer, a.protocol, c.reg_num,
       CASE WHEN abs(a.gazette_year - extract(year FROM a.accepted)) <= 1 THEN a.gazette_number END,
       CASE WHEN abs(a.gazette_year - extract(year FROM a.accepted)) <= 1 THEN a.gazette_year END, a.active, a.confidential, a.origin,
       t.tags, coalesce(r.n, 0),
       'data.egov.bg, АМС', 'https://data.egov.bg/data/view/18da0fff-79b2-45c6-a9af-1509df96261b', f.sha256,
       (a.first_seen_at AT TIME ZONE 'Europe/Sofia')::date, (a.changed_at AT TIME ZONE 'Europe/Sofia')::date
FROM zk_act a
JOIN ops.load l ON l.load_id = a.load_id
JOIN ops.raw_file f ON f.raw_file_id = l.raw_file_id
LEFT JOIN zk_cons c ON c.reg_num = a.public_consultation_number
LEFT JOIN (SELECT load_id, pris_id, array_agg(tag ORDER BY ord) AS tags FROM zakoni_src.pris_tag
           WHERE load_id IN (SELECT load_id FROM zk_current) GROUP BY 1, 2) t ON t.load_id = a.load_id AND t.pris_id = a.pris_id
LEFT JOIN (SELECT load_id, pris_id, count(*) AS n FROM zk_related GROUP BY 1, 2) r ON r.load_id = a.load_id AND r.pris_id = a.pris_id;

INSERT INTO zakoni.act_institution (pris_id, institution_id, name)
SELECT DISTINCT i.pris_id, i.institution_id, i.name FROM zakoni_src.pris_institution i
WHERE i.load_id IN (SELECT load_id FROM zk_current);

INSERT INTO zakoni.act_relation (pris_id, ord, relation_type, related_pris_id, related_in_data, act_type, act_name)
SELECT r.pris_id, r.ord, r.relation_type, r.related_pris_id, EXISTS (SELECT 1 FROM zakoni.act x WHERE x.pris_id = r.related_pris_id),
       r.act_type, r.act_name
FROM zk_related r WHERE r.related_pris_id <> r.pris_id;

INSERT INTO zakoni.consultation (reg_num, number, name, description, level, act_type, opened, closes, days, short_term,
                                 short_term_applies, short_term_reason, reason_given, policy_area, archived_area, institution_id,
                                 institution_name, municipality_code, law_name, law_id, act_pris_id, comment_count, files,
                                 source, source_url, raw_sha256, first_seen, last_seen)
SELECT c.reg_num, split_part(c.reg_num, '-', 1)::int, c.name, c.description, c.consultation_type, c.act_type, c.date_open,
       c.date_close, c.date_close - c.date_open,
       (c.date_close - c.date_open) < 30,
       c.date_open >= DATE '2016-11-04' AND coalesce(c.act_type, '') NOT LIKE 'Ненормативен%' AND (c.date_close - c.date_open) < 30,
       CASE WHEN btrim(c.short_term_reason, ' .-') = '' THEN NULL ELSE c.short_term_reason END,
       btrim(coalesce(c.short_term_reason, ''), ' .-') <> '',
       c.policy_area, c.policy_area LIKE 'Архив - %',
       c.institution_id, c.institution_name, m.municipality_code,
       c.law_name, c.law_id, a.pris_id, c.comment_count, coalesce(fc.n, 0),
       'data.egov.bg, АМС', 'https://strategy.bg/bg/public-consultations/' || split_part(c.reg_num, '-', 1), f.sha256,
       (c.first_seen_at AT TIME ZONE 'Europe/Sofia')::date, (c.changed_at AT TIME ZONE 'Europe/Sofia')::date
FROM zk_cons c
JOIN ops.load l ON l.load_id = c.load_id
JOIN ops.raw_file f ON f.raw_file_id = l.raw_file_id
LEFT JOIN zakoni.act a ON a.pris_id = c.pris_id
LEFT JOIN (SELECT reg_num, count(*) AS n FROM zakoni_src.consultation_file WHERE load_id IN (SELECT load_id FROM zk_current) GROUP BY 1) fc
       ON fc.reg_num = c.reg_num
LEFT JOIN (SELECT im.institution_id, cm.municipality_code FROM zakoni_src.institution_municipality im
           JOIN core.municipality cm ON cm.eik = im.municipality_eik
           WHERE im.load_id IN (SELECT load_id FROM zk_current)) m ON m.institution_id = c.institution_id;

INSERT INTO zakoni.consultation_file (reg_num, ord, kind, doc_date, link)
SELECT f.reg_num, f.ord, f.kind, f.doc_date, f.link FROM zakoni_src.consultation_file f
WHERE f.load_id IN (SELECT load_id FROM zk_current);

INSERT INTO zakoni.strategy_doc (doc_key, name, level, policy_area, doc_type, authority, act_pris_id, consultation_reg_num, accepted,
                                 expires, active, authors, files, source, source_url, raw_sha256, first_seen, last_seen)
SELECT s.doc_key, s.name, s.level, s.policy_area, s.doc_type, s.accepting_institution_type, a.pris_id, c.reg_num,
       s.date_accepted, s.date_expiring, s.active, u.authors, coalesce(fs.n, 0),
       'data.egov.bg, АМС', 'https://data.egov.bg/data/view/18da0fff-79b2-45c6-a9af-1509df96261b', f.sha256,
       (s.first_seen_at AT TIME ZONE 'Europe/Sofia')::date, (s.changed_at AT TIME ZONE 'Europe/Sofia')::date
FROM zk_strategy s
JOIN ops.load l ON l.load_id = s.load_id
JOIN ops.raw_file f ON f.raw_file_id = l.raw_file_id
LEFT JOIN zakoni.act a ON a.pris_id = s.pris_act_id
LEFT JOIN zakoni.consultation c ON c.reg_num = s.public_consultation_number
LEFT JOIN (SELECT doc_key, array_agg(institution ORDER BY ord) AS authors FROM zakoni_src.strategy_author
           WHERE load_id IN (SELECT load_id FROM zk_current) GROUP BY 1) u ON u.doc_key = s.doc_key
LEFT JOIN (SELECT doc_key, count(*) AS n FROM zakoni_src.strategy_file WHERE load_id IN (SELECT load_id FROM zk_current) GROUP BY 1) fs
       ON fs.doc_key = s.doc_key;

INSERT INTO zakoni.impact_contract (ic_key, institution_id, institution_name, contract_date, price_bgn, price_eur, eik, executor,
                                    executor_kind, subject, description, source, source_url, raw_sha256)
SELECT i.ic_key, i.institution_id, i.institution_name, i.contract_date, i.price_bgn, coalesce(i.price_eur, round(i.price_bgn / 1.95583, 2)), i.eik,
       i.executor, i.executor_kind, i.subject, i.description, 'data.egov.bg, АМС',
       'https://data.egov.bg/data/view/18da0fff-79b2-45c6-a9af-1509df96261b', f.sha256
FROM zakoni_src.impact_contract i
JOIN ops.load l ON l.load_id = i.load_id
JOIN ops.raw_file f ON f.raw_file_id = l.raw_file_id
WHERE i.load_id IN (SELECT load_id FROM zk_current);

-- keys that point nowhere: the row stays with an empty key, the reason is kept
INSERT INTO zakoni.unmatched (tbl, key, field, value, reason)
SELECT 'act', a.pris_id::text, 'public_consultation_number', s.public_consultation_number, 'консултацията я няма в справката'
FROM zk_act s JOIN zakoni.act a USING (pris_id) WHERE s.public_consultation_number IS NOT NULL AND a.consultation_reg_num IS NULL;

INSERT INTO zakoni.unmatched (tbl, key, field, value, reason)
SELECT 'consultation', c.reg_num, 'pris_id', s.pris_id::text, 'актът го няма в ПРИС'
FROM zk_cons s JOIN zakoni.consultation c USING (reg_num) WHERE s.pris_id IS NOT NULL AND c.act_pris_id IS NULL;

INSERT INTO zakoni.unmatched (tbl, key, field, value, reason)
SELECT 'consultation', c.reg_num, 'municipality_id', c.institution_name, 'общинска институция без община в справочника'
FROM zakoni.consultation c WHERE c.level = 'Общинско' AND c.municipality_code IS NULL AND c.institution_name IS NOT NULL;

INSERT INTO zakoni.unmatched (tbl, key, field, value, reason)
SELECT 'consultation', c.reg_num, 'institution_name', NULL, 'справката не дава име на институцията (institution_id ' || c.institution_id || ')'
FROM zakoni.consultation c WHERE c.institution_name IS NULL;

INSERT INTO zakoni.unmatched (tbl, key, field, value, reason)
SELECT 'strategy_doc', s.doc_key, 'pris_act_id', x.pris_act_id::text, 'актът го няма в ПРИС'
FROM zk_strategy x JOIN zakoni.strategy_doc s USING (doc_key) WHERE x.pris_act_id IS NOT NULL AND s.act_pris_id IS NULL;

INSERT INTO zakoni.unmatched (tbl, key, field, value, reason)
SELECT 'impact_contract', i.ic_key, 'eik', NULL, 'няма ЕИК (физическо лице или липсва)' FROM zakoni.impact_contract i WHERE i.eik IS NULL;

INSERT INTO zakoni.unmatched (tbl, key, field, value, reason)
SELECT 'act', a.pris_id::text, 'gazette', coalesce(s.gazette_number::text, '') || ' / ' || coalesce(s.gazette_year_raw, ''),
       'годината на ДВ не се определя или не е около годината на акта (' || extract(year FROM a.accepted)::int || '): броят и годината не се показват'
FROM zk_act s JOIN zakoni.act a USING (pris_id) WHERE (s.gazette_number IS NOT NULL OR s.gazette_year_raw IS NOT NULL) AND a.gazette_year IS NULL;

INSERT INTO zakoni.unmatched (tbl, key, field, value, reason)
SELECT 'act_relation', r.pris_id || '/' || r.ord, 'related_pris_id', r.related_pris_id::text, 'актът е посочен като свързан със себе си: връзката не се показва'
FROM zk_related r WHERE r.related_pris_id = r.pris_id
