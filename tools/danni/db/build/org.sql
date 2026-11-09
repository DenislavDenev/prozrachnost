-- core.org from the current load of the organisations of Тендер. Runs in one transaction.
-- Only rows that changed are rewritten; an organisation that left the source gets gone_at and stays.
INSERT INTO core.org (eik, name, legal_form, seat, registered_on, register_status, is_public_body, is_buyer, is_person_named, source)
SELECT o.eik, o.name, o.legal_form, o.seat, o.registered_on, o.register_status, o.is_public_body, o.is_buyer,
       o.legal_form IS NOT DISTINCT FROM 'ET', CASE WHEN o.register_status = 'ok' THEN 'tr' ELSE 'eop' END
FROM danni_src.org_tender o JOIN ops.load l USING (load_id)
WHERE l.is_current
ORDER BY o.eik
ON CONFLICT (eik) DO UPDATE SET name = EXCLUDED.name, legal_form = EXCLUDED.legal_form, seat = EXCLUDED.seat,
  registered_on = EXCLUDED.registered_on, register_status = EXCLUDED.register_status,
  is_public_body = EXCLUDED.is_public_body, is_buyer = EXCLUDED.is_buyer, is_person_named = EXCLUDED.is_person_named,
  source = EXCLUDED.source, gone_at = NULL
WHERE (core.org.name, core.org.legal_form, core.org.seat, core.org.registered_on, core.org.register_status,
       core.org.is_public_body, core.org.is_buyer, core.org.is_person_named, core.org.source, core.org.gone_at)
      IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.legal_form, EXCLUDED.seat, EXCLUDED.registered_on, EXCLUDED.register_status,
                        EXCLUDED.is_public_body, EXCLUDED.is_buyer, EXCLUDED.is_person_named, EXCLUDED.source, NULL::timestamptz);

UPDATE core.org c SET gone_at = now()
WHERE c.gone_at IS NULL AND NOT EXISTS (
  SELECT FROM danni_src.org_tender o JOIN ops.load l USING (load_id)
  WHERE l.is_current AND o.eik = c.eik);
