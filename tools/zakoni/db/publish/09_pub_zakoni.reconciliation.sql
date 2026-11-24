-- the newest result of every count: a loaded file against the landing zone (stops the publication), the other reports
-- of the source (a known difference of the source, or information)
SELECT name, checked_at, expected, actual, ok,
       CASE WHEN gating THEN 'block' WHEN name = 'consultations_vs_institution_report' THEN 'known' ELSE 'info' END,
       coalesce(detail, title_bg)
FROM ops.check_latest
WHERE tool = 'zakoni' AND (name LIKE 'records_%' OR name LIKE '%_report') AND checked_at IS NOT NULL
