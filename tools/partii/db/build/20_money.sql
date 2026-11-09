-- What every table says now about each participant. Money and non-monetary value stay apart; the report keeps the
-- currency it was filed in with the euro beside it (NULL while the currency of the reports of the election is not known).
DELETE FROM partii.observation;
DELETE FROM partii.participant_money;
INSERT INTO partii.participant_money (participant_id, donation_count, donated_money, donated_in_kind, over_threshold, over_threshold_no_decl,
                                      candidate_rows, candidate_money, candidate_in_kind, agency_count,
                                      report_income, report_expense, report_total, report_currency, report_income_eur, report_expense_eur, report_total_eur)
SELECT p.participant_id,
       coalesce(d.n, 0), coalesce(d.m, 0), coalesce(d.v, 0), coalesce(d.over, 0), coalesce(d.over_none, 0),
       coalesce(f.n, 0), coalesce(f.m, 0), coalesce(f.v, 0), coalesce(a.n, 0),
       r.income, r.expense, r.total, CASE WHEN r.participant_id IS NOT NULL THEN e.report_currency END,
       core.to_eur(r.income, e.report_currency, e.election_day), core.to_eur(r.expense, e.report_currency, e.election_day), core.to_eur(r.total, e.report_currency, e.election_day)
FROM partii.participant p
JOIN partii.election e USING (erik_id)
LEFT JOIN (SELECT d.participant_id, count(*) AS n, sum(d.money) AS m, sum(d.in_kind) AS v,
                  count(*) FILTER (WHERE d.money + d.in_kind >= el.threshold_eur) AS over,
                  count(*) FILTER (WHERE d.money + d.in_kind >= el.threshold_eur AND d.decl = 'none') AS over_none
           FROM partii.as_of('infinity') s JOIN partii.donation d USING (load_id) JOIN partii.election el ON el.erik_id = d.erik_id
           WHERE s.tbl = 'donors' GROUP BY d.participant_id) d USING (participant_id)
LEFT JOIN (SELECT f.participant_id, count(*) AS n, sum(f.money) AS m, sum(f.in_kind) AS v
           FROM partii.as_of('infinity') s JOIN partii.candidate_fund f USING (load_id) WHERE s.tbl = 'candidates' GROUP BY f.participant_id) f USING (participant_id)
LEFT JOIN (SELECT u.participant_id, count(DISTINCT coalesce(u.agency_eik, u.agency_name)) AS n
           FROM partii.as_of('infinity') s JOIN partii.agency_use u USING (load_id) WHERE s.tbl = 'agencies' GROUP BY u.participant_id) a USING (participant_id)
LEFT JOIN (SELECT r.participant_id,
                  max(c.amount) FILTER (WHERE k.code = 'income.total') AS income, max(c.amount) FILTER (WHERE k.code = 'expense.total') AS expense,
                  max(c.amount) FILTER (WHERE k.code = 'total') AS total
           FROM partii.as_of('infinity') s JOIN partii.report_row r USING (load_id)
           JOIN partii.report_cell c ON c.load_id = r.load_id AND c.row_no = r.row_no
           JOIN partii.report_column k ON k.layout = r.layout AND k.col = c.col
           WHERE s.tbl = 'report' AND r.participant_id IS NOT NULL AND k.code IN ('income.total', 'expense.total', 'total')
           GROUP BY r.participant_id HAVING count(DISTINCT (r.load_id, r.row_no)) = 1) r USING (participant_id);
