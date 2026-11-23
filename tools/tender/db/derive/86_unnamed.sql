-- A supplier the source gives by name only, without an ЕИК and without a legal form or the word of an organisation, may
-- be a natural person (the owner's decision of 23.11.2026, the list of cases of the project). The contract is a public
-- document and names its supplier, so the name stays on the contract. It does not become a profile: such a party has no
-- company page, no statistics, no tags, no search result and no line of its own in a ranking. In the serving layer
-- all of them are the one party_key 'unnamed'. A company without a form in its name falls here too; that is the safe side.
SET search_path = tender_stage, public;

CREATE OR REPLACE FUNCTION pg_temp.maybe_person(name text) RETURNS boolean LANGUAGE sql IMMUTABLE AS $f$
  SELECT name !~* '(^|[^а-яa-z])(е?оод|е?ад|ет|дзззд|дзз|дзд|кд|кда|сд|адсиц|ltd|limited|gmbh|llc|inc|corp|s\.?a\.?|s\.?r\.?l\.?|b\.?v\.?|ag|kg|oy|ab|d\.?o\.?o\.?|sp|a\.?s\.?|nv|plc|co)($|[^а-яa-z])'
     AND name !~* '(обединени|консорциум|сдружени|фондаци|кооперац|дружеств|общин|министерств|агенци|университет|болниц|училищ|институт|академи|банк|център|служба|дирекц|търговец|компания|company|group|consort|association|foundation|university|gesellschaft|holding|services|solutions|systems|technolog|мбал|дкц|читалищ|предприяти|завод|фирма|съюз|камара|кантора|бюро|ателие|студио)'
$f$;

CREATE TABLE unnamed_party AS
SELECT DISTINCT party_key FROM (SELECT party_key, eik, name FROM contract_supplier UNION ALL SELECT party_key, eik, name FROM subcontract) p
WHERE party_key LIKE 'name:%' AND eik IS NULL AND pg_temp.maybe_person(name);
ALTER TABLE unnamed_party ADD PRIMARY KEY (party_key);
COMMENT ON TABLE unnamed_party IS 'Изпълнители само с име, без ЕИК и правна форма: може да са физически лица. Името остава в договора, профил нямат.';

-- The same names outside the contract: a bidder or a member of a consortium without an ЕИК, and a payment to exactly such
-- a name. The offer of a procedure is not the contract, so there the name is replaced and the bidders stay distinct.
CREATE TABLE unnamed_name AS
SELECT DISTINCT upper(name) AS upper_name FROM (
  SELECT s.name FROM contract_supplier s JOIN unnamed_party u USING (party_key)
  UNION ALL SELECT s.name FROM subcontract s JOIN unnamed_party u USING (party_key)
  UNION ALL SELECT bidder_name FROM offer WHERE bidder_eik IS NULL AND company_key IS NULL AND bidder_name <> 'Физическо лице' AND pg_temp.maybe_person(bidder_name)
  UNION ALL SELECT m->>'name' FROM offer o, jsonb_array_elements(coalesce(o.consortium, '[]')) m
    WHERE coalesce(m->>'eik', '') = '' AND m->>'name' <> 'Физическо лице' AND pg_temp.maybe_person(m->>'name')
) n WHERE name IS NOT NULL;
ALTER TABLE unnamed_name ADD PRIMARY KEY (upper_name);
COMMENT ON TABLE unnamed_name IS 'Имена без ЕИК и правна форма. В офертите и плащанията се заменят; не се публикуват.';
