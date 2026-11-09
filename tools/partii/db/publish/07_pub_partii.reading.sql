-- pub_partii.reading: what the pages read (written only by pipeline.publish)
SELECT load_id, erik_id, tbl, variant, read_at, status, sha256, records_total, total_money, total_in_kind, total_sum, checked, compliant FROM partii.reading
