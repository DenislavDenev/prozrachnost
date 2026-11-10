SELECT 'tr_done', count(*) FILTER (WHERE status = 'done'), max(done_at) FROM tender_src.tr_queue
UNION ALL SELECT 'tr_pending', count(*) FILTER (WHERE status = 'pending'), NULL FROM tender_src.tr_queue
UNION ALL SELECT 'tr_failed', count(*) FILTER (WHERE status = 'failed'), NULL FROM tender_src.tr_queue
UNION ALL SELECT 'deeds_ok', count(*) FILTER (WHERE status = 'ok'), NULL FROM tender_src.tr_deed
UNION ALL SELECT 'deeds_absent', count(*) FILTER (WHERE status = 'absent'), NULL FROM tender_src.tr_deed
UNION ALL SELECT 'names_done', count(*) FILTER (WHERE status = 'done'), NULL FROM tender_src.tr_name_search
UNION ALL SELECT 'names_ambiguous', count(*) FILTER (WHERE status = 'ambiguous'), NULL FROM tender_src.tr_name_search
UNION ALL SELECT 'names_total', count(DISTINCT name_key), NULL FROM tender_src.tr_person
