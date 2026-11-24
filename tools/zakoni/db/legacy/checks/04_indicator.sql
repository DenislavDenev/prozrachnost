-- the short-term indicator is days under 30 for a draft normative act opened since 04.11.2016; days under 14 is shown apart (below the legal floor)
SELECT 'indicator-without-days' AS check, reg_num AS key, days::text AS detail FROM gold.consultation WHERE short_term_applies AND days >= 30
UNION ALL SELECT 'indicator-before-the-rule', reg_num, opened::text FROM gold.consultation WHERE short_term_applies AND opened < DATE '2016-11-04'
UNION ALL SELECT 'reason-given-without-text', reg_num, '' FROM gold.consultation WHERE reason_given AND short_term_reason IS NULL;
