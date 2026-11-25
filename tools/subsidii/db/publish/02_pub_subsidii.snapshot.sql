-- pub_subsidii.snapshot: every file of the archive and its check (no names)
SELECT fy, day, status, sha256, path, bytes, encoding, rows_file, holders, payments, total_efgz, total_ezfrs, total_nb, total_all,
       paid_efgz, paid_ezfrs, paid_nb, block_diffs, negatives, rows_added, rows_removed, note, built_at, build_secs
FROM subsidii.snapshot
