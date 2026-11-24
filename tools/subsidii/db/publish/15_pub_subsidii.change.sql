-- pub_subsidii.change: the newest 50 lines of the ledger of changes of the source (by year: counts, sums and sha256, no names)
SELECT change_id, detected_at, ref, field, old, new, cause
FROM ops.change_log WHERE tool = 'subsidii' ORDER BY change_id DESC LIMIT 50
