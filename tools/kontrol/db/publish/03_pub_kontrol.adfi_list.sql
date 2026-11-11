SELECT record_id, source, categories, CASE WHEN 'title' = ANY(private_fields) THEN 'Източников запис: лични данни не се преиздават' ELSE title END AS title, url, kind, report_period, inspection_count, inspection_status
FROM kontrol.adfi_list
