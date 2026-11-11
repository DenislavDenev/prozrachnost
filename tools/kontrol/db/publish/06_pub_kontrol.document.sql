SELECT record_id, url, document_sha256, text_available, CASE WHEN 'excerpt' = ANY(private_fields) THEN 'Източников запис: лични данни не се преиздават' ELSE excerpt END AS excerpt, excerpt_page, text_status, unp, fetch_url,
       fetch_method, read_at
FROM kontrol.document
