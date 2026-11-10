-- pub_partii.donation: what the pages read (written only by pipeline.publish)
SELECT load_id, row_no, erik_id, row_key, ordinal, participant_id, donation_day, kind, stated_kind, money, in_kind, decl, donor_kind, donor_name
FROM partii.donation d WHERE EXISTS (SELECT 1 FROM partii.reading r WHERE r.load_id = d.load_id)
