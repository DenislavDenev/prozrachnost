SELECT record_id, source, categories, CASE WHEN 'title' = ANY(private_fields) THEN 'Източников запис: лични данни не се преиздават' ELSE title END AS title, url, kind, act_no, act_date, case_no, source_case_id, published_on,
       publication_year, law, CASE WHEN 'outcome_text' = ANY(private_fields) THEN 'Източников запис: лични данни не се преиздават' ELSE outcome_text END AS outcome_text, unp, source_gap
FROM kontrol.decision
