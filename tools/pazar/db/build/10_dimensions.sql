-- The dimensions of the daily observations, from the clean tables and the shared register. Run before every build of
-- the observations; cheap (a few hundred chains, a few thousand stores).
-- a chain whose only shop says in its name that it is online (an address with .bg or the word "онлайн") sells to the whole country
UPDATE pazar.chain c SET national_price = x.national
FROM (SELECT c2.chain_id, coalesce((SELECT count(*) = 1 AND bool_and(s.name ~* '(онлайн|online|\.bg)') FROM pazar.store s WHERE s.chain_id = c2.chain_id), false) AS national
      FROM pazar.chain c2) x
WHERE x.chain_id = c.chain_id AND c.national_price IS DISTINCT FROM x.national;

-- the same ЕИК in the shared register of organisations, when it is there
UPDATE pazar.chain c SET org_eik = o.eik FROM core.org o WHERE o.eik = c.eik AND c.org_eik IS DISTINCT FROM o.eik;

-- the settlement of a store, when its code is in ЕКАТТЕ
UPDATE pazar.store s SET settlement_ekatte = p.ekatte FROM core.settlement p
WHERE p.ekatte = s.ekatte AND s.settlement_ekatte IS DISTINCT FROM p.ekatte;
UPDATE pazar.store s SET settlement_ekatte = NULL
WHERE s.settlement_ekatte IS NOT NULL AND NOT EXISTS (SELECT 1 FROM core.settlement p WHERE p.ekatte = s.ekatte);
