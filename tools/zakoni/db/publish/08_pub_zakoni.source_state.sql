SELECT s.*, (SELECT max(l.detected_at) FROM ops.change_log l WHERE l.tool = 'zakoni' AND l.ref = s.ref)
FROM zakoni.source_state s
