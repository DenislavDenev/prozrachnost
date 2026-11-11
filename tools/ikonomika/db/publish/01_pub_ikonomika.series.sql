SELECT o.series_id, substr(s.indicator_code, 11), s.dims, s.geo, o.period, o.value, o.flag
FROM ikonomika.observation o JOIN ikonomika.series s USING (series_id)
WHERE o.superseded_at IS NULL
