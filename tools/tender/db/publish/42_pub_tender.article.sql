SELECT id, entity_type, entity_id, url, title, source, published_at, status FROM tender_src.ed_article
WHERE status = 'confirmed' AND entity_type = 'company'
