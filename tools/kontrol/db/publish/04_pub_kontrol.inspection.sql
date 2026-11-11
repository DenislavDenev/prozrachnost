SELECT record_id, source, categories, list_id, source_row, CASE WHEN 'title' = ANY(private_fields) THEN 'Източников запис: лични данни не се преиздават' ELSE title END AS title, url, kind, CASE WHEN 'auditee' = ANY(private_fields) THEN 'Източников запис: лични данни не се преиздават' ELSE auditee END AS auditee, eik, report_period,
       document_page, document_sha256, CASE WHEN 'excerpt' = ANY(private_fields) THEN 'Източников запис: лични данни не се преиздават' ELSE excerpt END AS excerpt, excerpt_page, result_text, violations_count, detail_status,
       report_no, result_page
FROM kontrol.inspection
