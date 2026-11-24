-- the last finished runs of the steps (the run that publishes is not finished yet: it is in the next publication)
SELECT run_id, step, started_at, finished_at, status FROM ops.run
WHERE tool = 'pazar' AND status <> 'skipped' AND finished_at IS NOT NULL ORDER BY run_id DESC LIMIT 12
