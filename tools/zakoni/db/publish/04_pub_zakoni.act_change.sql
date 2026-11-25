-- what the source changed in an act after it was first read, field by field (ingest/store.py logs it with ref pris_act/<id>)
SELECT change_id, split_part(ref, '/', 2)::int, detected_at, field, old, new, cause
FROM ops.change_log
WHERE tool = 'zakoni' AND source = 'egov' AND ref ~ '^pris_act/[0-9]+$' AND cause IN ('new-record', 'rewritten', 'removed')
