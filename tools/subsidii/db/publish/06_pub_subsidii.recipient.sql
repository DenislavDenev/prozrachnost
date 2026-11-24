-- pub_subsidii.recipient: the ОБЩО rows of the spans a good build shows. A legal entity with its name. A natural person or a
-- sole trader only above the threshold of the source (1 250 euro in the year), masked ("Иван ***") until two years after
-- the end of the year and without a name after that; at the threshold or below it only in pub_subsidii.below_threshold.
SELECT h.holder_id, h.fy, h.from_day, h.to_day, g.kind, g.org_id, g.municipality_code, b.oblast, b.obshtina,
       CASE WHEN g.kind = 'legal' THEN b.name::text END,
       CASE WHEN g.kind <> 'legal' AND subsidii.today() < y.names_until
            THEN core.mask_name(btrim(b.name || CASE WHEN b.surname = '-' THEN '' ELSE ' ' || b.surname END)) END,
       h.efgz, h.ezfrs, h.nb, h.ezfrs_nb, h.total
FROM subsidii_src.holder h
JOIN subsidii_src.beneficiary b USING (beneficiary_id)
JOIN subsidii.beneficiary g USING (beneficiary_id)
JOIN subsidii.fiscal_year y ON y.fy = h.fy
WHERE (g.kind = 'legal' OR (y.eur_per_unit IS NOT NULL AND h.total * y.eur_per_unit > subsidii.name_threshold_eur()))
  AND EXISTS (SELECT 1 FROM subsidii.build k WHERE k.fy = h.fy AND k.ok AND k.snap_day >= h.from_day AND (h.to_day IS NULL OR k.snap_day <= h.to_day))
