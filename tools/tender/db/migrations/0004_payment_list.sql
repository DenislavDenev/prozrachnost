-- The list of payments is ordered by the day, newest first, and then by the key of the row. With an index on the
-- day only, the list sorted all 1,9 million rows and ran 13,8 s against a limit of 15 s (24.11.2026, a 500).
CREATE INDEX payment_list_idx ON pub_tender.payment (settlement_date DESC NULLS LAST, resource_uri, row_no);
