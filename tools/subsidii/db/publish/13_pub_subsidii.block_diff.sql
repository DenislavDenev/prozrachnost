-- pub_subsidii.block_diff: the inconsistencies of the source; a person is "физическо лице"
SELECT d.fy, d.day, d.line_no, d.fund, d.stated, d.summed, g.kind,
       CASE WHEN g.kind = 'legal' THEN b.name::text WHEN g.kind = 'sole_trader' THEN 'физическо лице (едноличен търговец)' ELSE 'физическо лице' END,
       b.oblast, b.obshtina
FROM subsidii_src.block_diff d JOIN subsidii_src.beneficiary b USING (beneficiary_id) JOIN subsidii.beneficiary g USING (beneficiary_id)
