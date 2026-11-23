SELECT entity_type, entity_id, code, reason, evidence FROM tender.tag t WHERE NOT (t.entity_type = 'company' AND EXISTS (SELECT FROM tender.unnamed_party u WHERE u.party_key = t.entity_id))
