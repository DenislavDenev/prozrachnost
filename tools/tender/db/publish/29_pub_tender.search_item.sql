SELECT kind, ref, label, sub, key, weight, words FROM tender.search_item WHERE kind <> 'person'
UNION ALL
SELECT kind, ref, label, sub, key, weight, to_tsvector('simple', key) FROM tender.shown_person_search
