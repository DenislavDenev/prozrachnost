-- Buyers placed on the municipality map (methodology 7). A buyer is placed in a municipality when
--   'municipality': it is the municipality itself, or a unit whose 13-digit BULSTAT starts with the
--                   municipality's ЕИК (Sofia and Plovdiv districts, municipal schools and the like);
--   'address':      its OCDS address names a town that is also the name of exactly one municipality
--                   (гр. София -> Столична). Villages need the EKATTE register and stay unplaced.
SET search_path = stage, public;

CREATE INDEX ON source_record (entity, ref);

CREATE TABLE buyer_place AS
WITH direct AS (
  SELECT b.eik, m.drawn_as AS municipality, 'municipality' AS basis
  FROM buyer b JOIN municipality m ON m.id = b.eik OR m.id = left(b.eik, 9)
), town AS (
  SELECT b.eik, regexp_replace(upper(trim(b.locality)), '^(ГР\.|ГРАД)\s*', '') AS town
  FROM buyer b WHERE b.locality IS NOT NULL AND b.locality !~* '^\s*(с\.|село|к\.|кв\.)'
), named AS (
  SELECT upper(name_bg) AS town, min(drawn_as) AS municipality FROM municipality
  GROUP BY 1 HAVING count(DISTINCT drawn_as) = 1
  UNION ALL SELECT 'СОФИЯ', (SELECT drawn_as FROM municipality WHERE name_bg = 'Столична')
)
SELECT DISTINCT ON (eik) eik, municipality, basis FROM (
  SELECT eik, municipality, basis, 1 AS pref FROM direct
  UNION ALL
  SELECT t.eik, n.municipality, 'address', 2 FROM town t JOIN named n ON n.town = t.town
) x ORDER BY eik, pref;
ALTER TABLE buyer_place ADD PRIMARY KEY (eik);
CREATE INDEX ON buyer_place (municipality);
