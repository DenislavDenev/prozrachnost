-- the tables read now that hold a text where a number belongs, or whose banner names another year than their set
SELECT r.resource_uri, r.set_uri, d.year, r.family, r.n_rows, r.issues, r.title_year
FROM sigurnost_src.current_resource r JOIN sigurnost.dataset d USING (set_uri)
WHERE r.kind = 'table' AND (r.issues > 0 OR (r.title_year IS NOT NULL AND r.title_year IS DISTINCT FROM d.year))
