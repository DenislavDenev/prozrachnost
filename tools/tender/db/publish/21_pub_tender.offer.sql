SELECT o.unp, o.tender_id, o.lot_no, o.round, o.offer_id,
  CASE WHEN n.upper_name IS NULL THEN o.bidder_name
       ELSE 'Участник без ЕИК и правна форма ' || dense_rank() OVER (PARTITION BY o.unp, n.upper_name IS NULL ORDER BY n.upper_name) END,
  o.bidder_eik,
  (SELECT jsonb_agg(CASE WHEN coalesce(m->>'eik', '') = '' AND EXISTS (SELECT FROM tender.unnamed_name u WHERE u.upper_name = upper(m->>'name'))
                         THEN jsonb_set(m, '{name}', '"Член без ЕИК и правна форма"') ELSE m END ORDER BY i)
   FROM jsonb_array_elements(o.consortium) WITH ORDINALITY x(m, i)),
  o.submitted_at, o.price, o.price_opened, o.currency, o.price_eur, o.company_key, o.won, o.fetched_at
FROM tender.offer o
LEFT JOIN tender.unnamed_name n ON n.upper_name = upper(o.bidder_name) AND o.bidder_eik IS NULL AND o.company_key IS NULL
