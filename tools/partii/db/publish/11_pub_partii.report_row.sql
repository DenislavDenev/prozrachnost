-- pub_partii.report_row: what the pages read (written only by pipeline.publish)
SELECT load_id, row_no, erik_id, layout, participant_label, participant_id, filled
FROM partii.report_row d WHERE EXISTS (SELECT 1 FROM partii.reading r WHERE r.load_id = d.load_id)
