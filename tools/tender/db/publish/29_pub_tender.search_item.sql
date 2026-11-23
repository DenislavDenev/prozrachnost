SELECT kind, ref, label, sub, key, weight, words FROM tender.search_item s WHERE kind <> 'person' AND NOT (kind = 'company' AND EXISTS (SELECT FROM tender.unnamed_party u WHERE u.party_key = s.ref))
UNION ALL
SELECT kind, ref, label, sub, key, weight, to_tsvector('simple', key) FROM tender.shown_person_search
