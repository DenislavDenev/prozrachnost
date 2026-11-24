-- the share of rows without a key must not jump: municipal consultations without a municipality are known and few
SELECT 'municipal-without-municipality-over-2-percent' AS check, 'consultation' AS key, count(*)::text AS detail
FROM gold.unmatched u WHERE u.build_id = (SELECT max(build_id) FROM gold.build) AND u.field = 'municipality_id'
HAVING count(*) > 0.02 * (SELECT count(*) FROM gold.consultation WHERE level = 'Общинско');
