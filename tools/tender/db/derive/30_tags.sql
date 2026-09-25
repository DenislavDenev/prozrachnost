-- Automatic tags (methodology 4). Every tag row carries the numbers behind it in `evidence` and a
-- human-readable `reason`. A tag is an indicator, not an accusation.
SET search_path = stage, public;

CREATE TABLE tag_def (
  code text PRIMARY KEY, kind text NOT NULL, label text NOT NULL, formula text NOT NULL,
  window_years int, min_n int, threshold numeric, version text NOT NULL
);
INSERT INTO tag_def VALUES
 ('single_bid_rate', 'statistical', 'Често единствен участник',
  'договори с 1 оферта / договори с известен брой оферти', 3, 5, 0.60, 'v1'),
 ('buyer_concentration', 'statistical', 'Концентрация при един възложител',
  'стойност при най-големия възложител / стойност при всички възложители', 3, 3, 0.50, 'v1'),
 ('amendment_inflation', 'statistical', 'Поскъпване с анекси',
  'има договор с текуща / първоначална стойност ≥ 1.2', NULL, 1, 1.2, 'v1'),
 ('near_estimate', 'statistical', 'Цена близо до прогнозната',
  'договори в ±1% от прогнозната / договори с прогнозна стойност', 3, 3, 0.60, 'v1'),
 ('young_company', 'registry', 'Нова фирма с голям договор',
  'регистрирана ≤ 365 дни преди договор ≥ 70 000 EUR', NULL, 1, 70000, 'v1'),
 ('consortium', 'registry', 'Участие в обединение', 'печелила като член на обединение', NULL, 1, NULL, 'v1'),
 ('subcontractor', 'registry', 'Подизпълнител', 'посочена като подизпълнител', NULL, 1, NULL, 'v1'),
 ('public_body', 'registry', 'Публичен възложител', 'ЕИК е възложител по ЗОП', NULL, 1, NULL, 'v1');

CREATE TABLE tag (
  entity_type text NOT NULL, entity_id text NOT NULL, code text NOT NULL REFERENCES tag_def,
  reason text NOT NULL, evidence jsonb NOT NULL,
  PRIMARY KEY (entity_type, entity_id, code)
);

-- contract lines per company within the 3-year window (by effective date)
CREATE TEMP TABLE w3 ON COMMIT DROP AS
SELECT s.party_key, c.* FROM contract_supplier s JOIN contract c ON c.id = s.contract_id
WHERE c.effective_date >= current_date - interval '3 years';

INSERT INTO tag
SELECT 'company', party_key, 'single_bid_rate',
       format('%s от %s договора с известен брой оферти за последните 3 г. са с една оферта',
              single, known),
       jsonb_build_object('single', single, 'known', known, 'unknown', unknown, 'window_years', 3)
FROM (SELECT party_key,
             count(DISTINCT id) FILTER (WHERE offers_count = 1) AS single,
             count(DISTINCT id) FILTER (WHERE offers_count IS NOT NULL) AS known,
             count(DISTINCT id) FILTER (WHERE offers_count IS NULL) AS unknown
      FROM w3 GROUP BY party_key) x
WHERE known >= 5 AND single::numeric / known >= 0.60;

INSERT INTO tag
SELECT 'company', party_key, 'buyer_concentration',
       format('%s%% от стойността на договорите за последните 3 г. е при един възложител (%s)',
              round(100 * top / total), top_buyer),
       jsonb_build_object('top_eur', round(top, 2), 'total_eur', round(total, 2), 'buyer_eik', top_buyer,
                          'contracts', n, 'window_years', 3)
FROM (SELECT party_key, sum(v) AS total, max(v) AS top, sum(n) AS n,
             (array_agg(buyer_eik ORDER BY v DESC))[1] AS top_buyer
      FROM (SELECT party_key, buyer_eik, sum(amount_eur) AS v, count(DISTINCT id) AS n
            FROM w3 WHERE amount_eur IS NOT NULL AND NOT is_framework GROUP BY 1, 2) b
      GROUP BY party_key) x
WHERE n >= 3 AND total > 0 AND top / total >= 0.50;

INSERT INTO tag
SELECT 'company', s.party_key, 'amendment_inflation',
       format('%s договора с текуща стойност ≥ 1.2 пъти първоначалната (най-много ×%s)',
              count(DISTINCT c.id), round(max(c.value_current / c.value_initial), 2)),
       jsonb_build_object('contracts', count(DISTINCT c.id),
                          'max_ratio', round(max(c.value_current / c.value_initial), 3),
                          'ids', (array_agg(DISTINCT c.id))[1:20])
FROM contract_supplier s JOIN contract c ON c.id = s.contract_id
WHERE c.value_initial > 0 AND c.value_current / c.value_initial >= 1.2
  AND c.value_flag NOT IN ('annex_suspect', 'value_suspect')
GROUP BY s.party_key;

INSERT INTO tag
SELECT 'company', party_key, 'near_estimate',
       format('%s от %s договора с прогнозна стойност за последните 3 г. са в ±1%% от нея', near, known),
       jsonb_build_object('near', near, 'known', known, 'window_years', 3)
FROM (SELECT party_key,
             count(DISTINCT id) FILTER (WHERE abs(value_initial_eur - estimated_eur) <= 0.01 * estimated_eur) AS near,
             count(DISTINCT id) FILTER (WHERE estimated_eur > 0 AND value_initial_eur IS NOT NULL) AS known
      FROM w3 WHERE value_flag = 'ok' GROUP BY party_key) x
WHERE known >= 3 AND near::numeric / known >= 0.60;

INSERT INTO tag
SELECT 'company', co.key, 'young_company',
       format('Регистрирана на %s, договор за %s EUR на %s (%s дни по-късно)',
              co.registered_on, round(c.amount_eur), c.effective_date, c.effective_date - co.registered_on),
       jsonb_build_object('registered_on', co.registered_on, 'contract_id', c.id,
                          'contract_date', c.effective_date, 'amount_eur', round(c.amount_eur, 2))
FROM company co
JOIN LATERAL (
  SELECT c.* FROM contract_supplier s JOIN contract c ON c.id = s.contract_id
  WHERE s.party_key = co.key AND c.amount_eur >= 70000 AND c.effective_date IS NOT NULL
    AND c.effective_date - co.registered_on BETWEEN 0 AND 365
  ORDER BY c.effective_date LIMIT 1) c ON true
WHERE co.registered_on IS NOT NULL;

INSERT INTO tag
SELECT 'company', party_key, 'consortium',
       format('Член на обединение в %s договора', count(DISTINCT contract_id)),
       jsonb_build_object('contracts', count(DISTINCT contract_id))
FROM contract_supplier WHERE joint GROUP BY party_key;

INSERT INTO tag
SELECT 'company', party_key, 'subcontractor',
       format('Подизпълнител по %s договора', count(DISTINCT contract_id)),
       jsonb_build_object('contracts', count(DISTINCT contract_id))
FROM subcontract GROUP BY party_key
ON CONFLICT DO NOTHING;

INSERT INTO tag
SELECT 'company', key, 'public_body', 'ЕИК е регистриран като възложител по ЗОП',
       jsonb_build_object('eik', eik)
FROM company WHERE is_public_body;

-- editorial tags (ed.tag) are joined at read time so an edit shows without a rebuild
