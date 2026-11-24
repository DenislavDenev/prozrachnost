-- pub_subsidii.municipality_fy: the sums of the good snapshots
SELECT f.fy, f.snap_day, f.municipality_code, f.kind, f.recipients, f.efgz, f.ezfrs, f.nb, f.total
FROM subsidii.municipality_fy f JOIN subsidii.build k ON k.fy = f.fy AND k.snap_day = f.snap_day AND k.ok
