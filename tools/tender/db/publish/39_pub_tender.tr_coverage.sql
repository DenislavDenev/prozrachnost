SELECT q.eik, q.status, q.done_at, d.fetched_at FROM tender_src.tr_queue q LEFT JOIN tender_src.tr_deed d ON d.eik = q.eik
