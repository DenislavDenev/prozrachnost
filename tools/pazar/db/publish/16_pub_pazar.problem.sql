-- the same list as the step freshness reports (pipeline.freshness), as of the publication
SELECT DISTINCT problem FROM (
  SELECT title_bg || ': ' || CASE WHEN last_ok_at IS NULL THEN 'няма успешно четене'
         ELSE 'последно успешно четене ' || to_char(last_ok_at AT TIME ZONE 'UTC', 'DD.MM.YYYY HH24:MI') || ' UTC' END AS problem
  FROM ops.freshness WHERE tool = 'pazar' AND is_stale
  UNION ALL
  SELECT h.source || ' ' || h.ref || ': задържан от ' || to_char(h.first_at AT TIME ZONE 'UTC', 'DD.MM.YYYY') || ' (' || coalesce(h.reason, '') || ')'
  FROM ops.hold h JOIN ops.source s USING (tool, source) WHERE h.tool = 'pazar' AND h.first_at < now() - s.hold_alert_after
  UNION ALL
  SELECT title_bg || ': очаквано ' || coalesce(expected::text, 'няма') || ', получено ' || coalesce(actual::text, 'няма')
  FROM ops.check_latest WHERE tool = 'pazar' AND gating AND kind <> 'parity' AND ok = false
) p
