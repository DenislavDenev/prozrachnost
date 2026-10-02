-- every violation is a row; one row stops the build (the name is the first column)
SELECT 'act-key-twice' AS check, pris_id::text AS key, count(*)::text AS detail FROM silver.pris_act WHERE valid_to IS NULL GROUP BY pris_id HAVING count(*) > 1
UNION ALL SELECT 'consultation-key-twice', reg_num, count(*)::text FROM silver.consultation WHERE valid_to IS NULL GROUP BY reg_num HAVING count(*) > 1;
