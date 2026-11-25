SELECT change_id, detected_at, ref, field, old, new, cause FROM ops.change_log WHERE tool = 'sigurnost' ORDER BY change_id DESC LIMIT 30
