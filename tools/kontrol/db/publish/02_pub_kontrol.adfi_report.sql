SELECT record_id, source, categories, CASE WHEN 'title' = ANY(private_fields) THEN 'Източников запис: лични данни не се преиздават' ELSE title END AS title, url, kind, CASE WHEN 'auditee' = ANY(private_fields) THEN 'Източников запис: лични данни не се преиздават' ELSE auditee END AS auditee, eik, basis, published_on, publication_year,
       updated_label
FROM kontrol.adfi_report
