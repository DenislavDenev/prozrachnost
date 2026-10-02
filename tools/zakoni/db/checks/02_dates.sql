SELECT 'consultation-closes-before-opens' AS check, reg_num AS key, opened || ' - ' || closes AS detail FROM gold.consultation WHERE closes < opened
UNION ALL SELECT 'consultation-days-not-positive', reg_num, days::text FROM gold.consultation WHERE days < 1
UNION ALL SELECT 'act-in-the-future', pris_id::text, accepted::text FROM gold.act WHERE accepted > current_date + 1
UNION ALL SELECT 'act-before-1944', pris_id::text, accepted::text FROM gold.act WHERE accepted < DATE '1944-01-01'
UNION ALL SELECT 'consultation-opened-in-the-future', reg_num, opened::text FROM gold.consultation WHERE opened > current_date + 1;
