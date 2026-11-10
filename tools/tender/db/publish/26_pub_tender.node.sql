SELECT id, kind, label, ref FROM tender.node WHERE kind IN ('company', 'foreign_entity')
UNION ALL
SELECT id, kind, label, ref FROM tender.shown_person_node
