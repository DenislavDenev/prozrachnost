SELECT 'act-without-type' AS check, pris_id::text AS key, '' AS detail FROM gold.act WHERE act_type = ''
UNION ALL SELECT 'gazette-year-out-of-range', pris_id::text, gazette_year::text FROM gold.act WHERE gazette_year IS NOT NULL AND (gazette_year < 1944 OR gazette_year > extract(year FROM current_date) + 1)
UNION ALL SELECT 'gazette-before-the-act', pris_id::text, gazette_year || ' < ' || year FROM gold.act WHERE gazette_year IS NOT NULL AND gazette_year < year - 1
UNION ALL SELECT 'relation-to-itself', pris_id::text, related_pris_id::text FROM gold.act_relation WHERE pris_id = related_pris_id
UNION ALL SELECT 'municipality-not-in-the-list', reg_num, municipality_id FROM gold.consultation c WHERE municipality_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM ref.municipality m WHERE m.id = c.municipality_id);
