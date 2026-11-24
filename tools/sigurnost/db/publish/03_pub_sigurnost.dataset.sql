-- the counts of each set: its tables read now, the answers refused as not a table, the ones the portal holds empty
SELECT d.set_uri, d.title, d.kind, d.year, d.terms_of_use_id, d.licence, d.shared, d.licence_checked, d.source_updated, d.listed,
       (SELECT count(*)::int FROM sigurnost_src.current_resource r WHERE r.set_uri = d.set_uri),
       (SELECT count(DISTINCT (l.ref, l.raw_file_id))::int FROM ops.load l JOIN sigurnost_src.list_entry e ON e.resource_uri = l.ref AND e.load_id = d.list_load_id
         WHERE l.tool = 'sigurnost' AND l.source = 'egov' AND l.status = 'rejected'),
       (SELECT count(*)::int FROM sigurnost_src.current_resource r WHERE r.set_uri = d.set_uri AND r.kind = 'empty'),
       (SELECT max(f.fetched_at) FROM ops.fetch f JOIN sigurnost_src.list_entry e ON e.resource_uri = f.ref AND e.load_id = d.list_load_id
         WHERE f.tool = 'sigurnost' AND f.source = 'egov')
FROM sigurnost.dataset d
