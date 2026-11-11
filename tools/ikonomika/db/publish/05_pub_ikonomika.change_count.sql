SELECT source, cause, count(*)::int FROM ops.change_log
WHERE tool = 'ikonomika' AND detected_at > now() - interval '30 days' GROUP BY 1, 2
