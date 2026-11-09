-- The clean tables from the landing zone (run by ingest/store.py in one transaction, after partii.election).
-- Participants are never deleted: a registration met in any accepted reading stays. The derived tables below
-- (labels, agencies, unmatched) say what the newest readings give and are written again on every build.

-- thresholds and counts of the pages shown now; a page that states no threshold does not erase the one known
UPDATE partii.election e SET listed_cik = f.listed_cik, listed_rik = f.listed_rik, listed_oik = f.listed_oik
FROM partii_src.page_fact f JOIN ops.load l USING (load_id)
WHERE l.tool = 'partii' AND l.source = 'page-participants' AND l.is_current AND f.erik_id = e.erik_id;
UPDATE partii.election e SET threshold_eur = f.threshold_eur, threshold_shown = f.threshold_shown
FROM partii_src.page_fact f JOIN ops.load l USING (load_id)
WHERE l.tool = 'partii' AND l.source = 'page-donation' AND l.is_current AND f.erik_id = e.erik_id AND f.threshold_eur IS NOT NULL;

-- a participant is known from the lists of participants and from the recipients of donations, by its number in ЕРИК
INSERT INTO partii.participant (erik_id, reg_in, source_id, kind, name, name_norm, registry_number, sub_election, municipality, settlement, city_area,
                                label, is_listed, first_seen_at, last_seen_at)
SELECT x.erik_id, x.reg_in, x.source_id, (array_agg(x.kind ORDER BY x.at DESC))[1], (array_agg(x.name ORDER BY x.at DESC))[1],
       btrim(regexp_replace(lower((array_agg(x.name ORDER BY x.at DESC))[1]), '[^[:alnum:]]+', ' ', 'g')),
       (array_agg(x.registry_number ORDER BY x.at DESC) FILTER (WHERE x.registry_number IS NOT NULL))[1],
       (array_agg(x.sub_election ORDER BY x.at DESC) FILTER (WHERE x.sub_election IS NOT NULL))[1],
       (array_agg(x.municipality ORDER BY x.at DESC) FILTER (WHERE x.municipality IS NOT NULL))[1],
       (array_agg(x.settlement ORDER BY x.at DESC) FILTER (WHERE x.settlement IS NOT NULL))[1],
       (array_agg(x.city_area ORDER BY x.at DESC) FILTER (WHERE x.city_area IS NOT NULL))[1],
       coalesce((array_agg(x.label ORDER BY x.at DESC) FILTER (WHERE x.as_recipient AND x.label IS NOT NULL))[1],
                (array_agg(x.label ORDER BY x.at DESC) FILTER (WHERE x.label IS NOT NULL))[1]),
       bool_or(NOT x.as_recipient), min(x.at), max(x.at)
FROM (
  SELECT p.erik_id, p.reg_in, p.source_id, p.kind, p.name, p.registry_number, p.sub_election, p.municipality, p.settlement, p.city_area,
         -- how the other tables of ЕРИК write a listed participant; NULL where the wording for the case is not known
         CASE WHEN p.municipality IS NULL AND (p.settlement IS NOT NULL OR p.city_area IS NOT NULL) THEN NULL
              WHEN p.settlement IS NOT NULL AND p.city_area IS NOT NULL THEN NULL
              ELSE p.name || ' - ' || CASE p.kind WHEN 'party' THEN 'Партия' WHEN 'coalition' THEN 'Коалиция' WHEN 'committee' THEN 'Инициативен комитет' ELSE 'Местна коалиция' END
                   || coalesce(', община ' || p.municipality, '') || coalesce(', кметство ' || p.settlement, '') || coalesce(', район ' || p.city_area, '') END AS label,
         false AS as_recipient, l.loaded_at AS at
  FROM partii_src.participant_row p JOIN ops.load l USING (load_id) WHERE l.status IN ('loaded', 'confirmed')
  UNION ALL
  SELECT DISTINCT d.erik_id, d.reg_in, d.source_id, d.recipient_kind, d.recipient_name, d.registry_number, NULL, NULL, NULL, NULL, d.recipient_label, true, l.loaded_at
  FROM partii_src.donation d JOIN ops.load l USING (load_id) WHERE l.status IN ('loaded', 'confirmed')) x
GROUP BY x.erik_id, x.reg_in, x.source_id
ON CONFLICT (erik_id, reg_in, source_id) DO UPDATE SET kind = EXCLUDED.kind, name = EXCLUDED.name, name_norm = EXCLUDED.name_norm,
  registry_number = EXCLUDED.registry_number, sub_election = EXCLUDED.sub_election, municipality = EXCLUDED.municipality, settlement = EXCLUDED.settlement,
  city_area = EXCLUDED.city_area, label = EXCLUDED.label, is_listed = EXCLUDED.is_listed, first_seen_at = EXCLUDED.first_seen_at, last_seen_at = EXCLUDED.last_seen_at;

-- a text links a row to a participant only when exactly one participant of the election has it
DELETE FROM partii.participant_label;
INSERT INTO partii.participant_label (erik_id, label, participant_id)
SELECT erik_id, label, min(participant_id) FROM partii.participant WHERE label IS NOT NULL GROUP BY erik_id, label HAVING count(*) = 1;

-- a report sheet names the participant by its registered name alone, or, in local elections, by the name, the place and the kind of election
DELETE FROM partii.report_label;
INSERT INTO partii.report_label (erik_id, layout, label, participant_id)
SELECT erik_id, kind, lbl, min(participant_id)
FROM (SELECT erik_id, kind, participant_id, name AS lbl FROM partii.participant WHERE reg_in = 1
      UNION ALL
      SELECT erik_id, kind, participant_id,
             btrim(regexp_replace(name || ' , община ' || municipality || coalesce(', кметство ' || settlement, '') || coalesce(', район ' || city_area, '')
                                  || ' ' || coalesce(sub_election, ''), '[[:space:]]+', ' ', 'g'))
      FROM partii.participant WHERE reg_in = 3 AND municipality IS NOT NULL) q
GROUP BY erik_id, kind, lbl HAVING count(*) = 1;

-- an agency is a legal entity only by a ЕИК with a right check digit; the rest stays in partii.unmatched
DELETE FROM partii.agency;
INSERT INTO partii.agency (eik, name, name_count, election_count, participant_count, row_count, org_eik)
SELECT u.agency_eik, mode() WITHIN GROUP (ORDER BY u.agency_name), count(DISTINCT u.agency_name), count(DISTINCT u.erik_id),
       count(DISTINCT (u.erik_id, u.participant_label)), count(*), (SELECT o.eik FROM core.org o WHERE o.eik = u.agency_eik)
FROM partii.as_of('infinity') s JOIN partii.agency_use u USING (load_id)
WHERE s.tbl = 'agencies' AND u.is_eik_valid GROUP BY u.agency_eik;

DELETE FROM partii.unmatched;
INSERT INTO partii.unmatched (kind, key, row_count, amount, reason)
SELECT 'agency', coalesce(u.agency_eik, 'без ЕИК') || ' | ' || u.agency_name, count(*), NULL::numeric,
       CASE WHEN bool_and(u.agency_eik IS NULL) THEN 'източникът не дава ЕИК' ELSE 'ЕИК с грешна контролна цифра' END
FROM partii.as_of('infinity') s JOIN partii.agency_use u USING (load_id) WHERE s.tbl = 'agencies' AND u.is_eik_valid IS NOT TRUE GROUP BY 2
UNION ALL
SELECT 'participant:candidates', f.erik_id || ' | ' || f.participant_label, count(*), round(sum(f.money + f.in_kind), 2), 'етикетът не сочи точно един участник от списъка'
FROM partii.as_of('infinity') s JOIN partii.candidate_fund f USING (load_id) WHERE s.tbl = 'candidates' AND f.participant_id IS NULL GROUP BY 2
UNION ALL
SELECT 'participant:agencies', u.erik_id || ' | ' || u.participant_label, count(*), NULL, 'етикетът не сочи точно един участник от списъка'
FROM partii.as_of('infinity') s JOIN partii.agency_use u USING (load_id) WHERE s.tbl = 'agencies' AND u.participant_id IS NULL GROUP BY 2
UNION ALL
SELECT 'participant:report', r.erik_id || ' | ' || r.layout || ' | ' || r.participant_label, count(*), NULL, 'името в справката не сочи точно един участник от списъка'
FROM partii.as_of('infinity') s JOIN partii.report_row r USING (load_id) WHERE s.tbl = 'report' AND r.participant_id IS NULL GROUP BY 2;
