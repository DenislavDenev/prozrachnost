-- The shared observation contract: one number per row, the original beside it, the address and the reading it comes
-- from. The period of every number is the election (its day, where known). The numbers of an election the archive
-- still reads daily are provisional. The history of a number is in the readings (partii.reading), not here.
INSERT INTO partii.observation (indicator_code, erik_id, participant_id, period_kind, period_start, period_end, amount, unit_code, amount_source, unit_source,
                                status, read_at, source_url, load_id)
SELECT i.indicator, s.erik_id, NULL::bigint, 'election', e.election_day, e.election_day, round(i.v, 2), i.unit, i.v, i.unit,
       CASE WHEN e.list_position <= %(recent)s THEN 'provisional' ELSE 'final' END, s.read_at,
       'https://erik.bulnao.government.bg/Reports/Donation?electionId=' || s.erik_id, s.load_id
FROM partii.as_of('infinity') s JOIN partii.election e USING (erik_id)
CROSS JOIN LATERAL (VALUES ('partii.donations.money', s.total_money, 'EUR'), ('partii.donations.in_kind', s.total_in_kind, 'EUR'),
                           ('partii.donations.count', s.records_total::numeric, 'count')) i(indicator, v, unit)
WHERE s.tbl = 'donors'
UNION ALL
SELECT i.indicator, s.erik_id, NULL, 'election', e.election_day, e.election_day, round(i.v, 2), 'EUR', i.v, 'EUR',
       CASE WHEN e.list_position <= %(recent)s THEN 'provisional' ELSE 'final' END, s.read_at,
       'https://erik.bulnao.government.bg/Reports/InElection?electionId=' || s.erik_id, s.load_id
FROM partii.as_of('infinity') s JOIN partii.election e USING (erik_id)
CROSS JOIN LATERAL (VALUES ('partii.candidates.money', s.total_money), ('partii.candidates.in_kind', s.total_in_kind)) i(indicator, v)
WHERE s.tbl = 'candidates'
UNION ALL
SELECT i.indicator, p.erik_id, p.participant_id, 'election', e.election_day, e.election_day, i.v, i.unit, i.v, i.unit,
       CASE WHEN e.list_position <= %(recent)s THEN 'provisional' ELSE 'final' END, s.read_at,
       'https://erik.bulnao.government.bg/Reports/Donation?electionId=' || s.erik_id, s.load_id
FROM partii.participant_money m JOIN partii.participant p USING (participant_id) JOIN partii.election e ON e.erik_id = p.erik_id
JOIN partii.as_of('infinity') s ON s.erik_id = p.erik_id AND s.tbl = 'donors'
CROSS JOIN LATERAL (VALUES ('partii.donations.money', m.donated_money, 'EUR'), ('partii.donations.in_kind', m.donated_in_kind, 'EUR'),
                           ('partii.donations.count', m.donation_count::numeric, 'count')) i(indicator, v, unit)
WHERE m.donation_count > 0
UNION ALL
SELECT i.indicator, p.erik_id, p.participant_id, 'election', e.election_day, e.election_day, i.eur, 'EUR', i.v, m.report_currency,
       'final', s.read_at, 'https://erik.bulnao.government.bg/Reports/AfterElection?electionId=' || p.erik_id, s.load_id
FROM partii.participant_money m JOIN partii.participant p USING (participant_id) JOIN partii.election e ON e.erik_id = p.erik_id
JOIN partii.as_of('infinity') s ON s.erik_id = p.erik_id AND s.tbl = 'report'
     AND s.variant = (CASE p.kind WHEN 'party' THEN '0' WHEN 'coalition' THEN '1' WHEN 'committee' THEN '2' ELSE '3' END)
CROSS JOIN LATERAL (VALUES ('partii.reports.income', m.report_income, m.report_income_eur), ('partii.reports.expense', m.report_expense, m.report_expense_eur)) i(indicator, v, eur)
WHERE i.v IS NOT NULL AND m.report_currency IS NOT NULL;
