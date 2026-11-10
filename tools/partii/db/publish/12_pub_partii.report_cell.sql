-- pub_partii.report_cell: what the pages read (written only by pipeline.publish)
SELECT load_id, row_no, col, amount_raw, amount
FROM partii.report_cell d WHERE EXISTS (SELECT 1 FROM partii.reading r WHERE r.load_id = d.load_id)
