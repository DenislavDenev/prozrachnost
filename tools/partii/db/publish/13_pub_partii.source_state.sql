-- pub_partii.source_state: what the pages read (written only by pipeline.publish)
SELECT load_id, source, ref, status, reason, loaded_at FROM partii.source_state
