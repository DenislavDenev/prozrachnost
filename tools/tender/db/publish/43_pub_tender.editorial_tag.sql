SELECT id, entity_type, entity_id, code, note, removed_at FROM tender_src.ed_tag
WHERE removed_at IS NULL AND entity_type <> 'person'
