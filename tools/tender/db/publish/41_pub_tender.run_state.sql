SELECT DISTINCT ON (step) step, status, started_at, finished_at, error FROM ops.run
WHERE tool = 'tender' AND step NOT IN ('replay', 'parity', 'publish', 'serve') ORDER BY step, started_at DESC, run_id DESC
