-- pub_subsidii.summary
SELECT s.fy, s.snap_day, s.scope, s.recipients, s.payments, s.efgz, s.ezfrs, s.nb, s.total, s.top1_n, s.top1_total
FROM subsidii.summary s JOIN subsidii.build k ON k.fy = s.fy AND k.snap_day = s.snap_day AND k.ok
