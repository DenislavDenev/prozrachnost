-- the lists of АДФИ whose format is only a document, as the current read of the historical register says
SELECT g->>'id', g->>'url', g->>'error', g->>'sha256'
FROM ops.load l JOIN kontrol_src.read r USING (load_id), jsonb_array_elements(r.manifest->'inspection_gaps') g
WHERE l.tool = 'kontrol' AND l.source = 'adfi-history' AND l.is_current
