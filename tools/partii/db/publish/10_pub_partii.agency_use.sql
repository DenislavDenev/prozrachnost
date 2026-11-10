-- pub_partii.agency_use: what the pages read (written only by pipeline.publish)
SELECT load_id, row_no, erik_id, participant_label, participant_id, agency_name, agency_eik, is_eik_valid, agency_kind, descr
FROM partii.agency_use d WHERE EXISTS (SELECT 1 FROM partii.reading r WHERE r.load_id = d.load_id)
