SELECT record_id, source, categories, CASE WHEN 'title' = ANY(private_fields) THEN 'Източников запис: лични данни не се преиздават' ELSE title END AS title, url, publication_year, report_period, kind, sector,
       CASE WHEN 'recommendation_text' = ANY(private_fields) THEN 'Източников запис: лични данни не се преиздават' ELSE recommendation_text END AS recommendation_text, eik, unp, source_discrepancy, source_kind_values, source_sector_values,
       source_publication_year_values
FROM kontrol.audit_report
