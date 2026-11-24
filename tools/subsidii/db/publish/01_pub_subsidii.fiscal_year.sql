-- pub_subsidii.fiscal_year: the years with their currency, rate, snapshots, the term and the threshold for names
SELECT fy, starts, ends, label_bg, currency, eur_per_unit, unit_evidence, in_form, gone_at, first_snap, last_snap, names_until,
       subsidii.name_threshold_eur()
FROM subsidii.fiscal_year
