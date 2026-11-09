-- pub_partii.candidate_fund: what the pages read (written only by pipeline.publish)
SELECT load_id, row_no, erik_id, participant_label, participant_id, role, fund_day, money, in_kind, decl
FROM partii.candidate_fund d WHERE EXISTS (SELECT 1 FROM partii.reading r WHERE r.load_id = d.load_id)
