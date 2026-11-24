-- pub_subsidii.payment_agg: the payments of the good snapshots by municipality, measure and kind
SELECT a.fy, a.snap_day, a.municipality_code, a.measure_id, a.kind, a.payments, a.efgz, a.ezfrs, a.nb, a.total
FROM subsidii.payment_agg a JOIN subsidii.build k ON k.fy = a.fy AND k.snap_day = a.snap_day AND k.ok
