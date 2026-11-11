-- the same list as the step freshness reports (pipeline.freshness), as of the publication
SELECT DISTINCT problem FROM (
  SELECT title_bg || ': ' || CASE WHEN last_ok_at IS NULL THEN 'няма успешно четене'
         ELSE 'последно успешно четене ' || to_char(last_ok_at AT TIME ZONE 'UTC', 'DD.MM.YYYY HH24:MI') || ' UTC' END AS problem
  FROM ops.freshness WHERE tool = 'byudzhet' AND is_stale
  UNION ALL
  SELECT title_bg || ': очаквано ' || expected || ', получено ' || actual
  FROM ops.check_latest WHERE tool = 'byudzhet' AND kind IN ('reconcile', 'unique', 'not_null', 'fk', 'unmatched', 'drop') AND ok = false
  UNION ALL
  SELECT ref || ': ' || status || COALESCE(': ' || error, '') FROM byudzhet.source_state
  WHERE status IN ('липсва при източника', 'задържан до второ четене')
     OR (status = 'невалиден отговор' AND NOT EXISTS (SELECT FROM byudzhet.known_gap g WHERE g.uri = ref AND g.sha256 = raw_sha256))
) p
