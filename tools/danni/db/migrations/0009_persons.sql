-- Names of people. The rule is about the role a name appears in, not about the person: the same person can be a
-- member of parliament in one dataset and a donor in another, and we do not know it is the same person.
--
--   public-office  a politician or an official, named by the source in that role      shown in full
--   public-money   a manager or an owner of an entity that receives public money      shown in full, in that role
--   registered     a private person in a public register by legal duty (a donor)      masked, summed, or absent
--   incidental     a contact person, a signatory                                      not kept
--
-- Three types make the rule something the database checks instead of something we remember:
--   core.person_name   the full name as the source wrote it; may not appear in the serving layer
--   pub.masked_name    "Иван ***": the first name and nothing else; the type refuses anything not masked
--   pub.role_name      a full name in the serving layer; allowed only for a column decided as public-office or public-money
-- Every column that holds a name is listed in core.person_column with the decision taken for it and why.

CREATE DOMAIN core.person_name AS text;
COMMENT ON DOMAIN core.person_name IS 'Пълното име на човек, както е в източника. Само в приемната част; в слоя за показване е забранено.';

CREATE DOMAIN pub.masked_name AS text CHECK (VALUE ~ '^(\S+ )?\*\*\*$');
COMMENT ON DOMAIN pub.masked_name IS 'Маскирано име: първото име и ***. Типът отказва всичко друго.';

CREATE DOMAIN pub.role_name AS text;
COMMENT ON DOMAIN pub.role_name IS 'Пълно име на човек в публичната му роля. Допустимо само за колона, решена като public-office или public-money.';

-- "Иван Петров Георгиев" -> "Иван ***". One word alone is not known to be a first name: "***".
CREATE FUNCTION core.mask_name(name text) RETURNS pub.masked_name
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN name IS NULL OR btrim(name) = '' THEN NULL
              WHEN btrim(name) ~ '\s' THEN split_part(regexp_replace(btrim(name), '\s+', ' ', 'g'), ' ', 1) || ' ***'
              ELSE '***' END::pub.masked_name
$$;
COMMENT ON FUNCTION core.mask_name(text) IS 'Маскира име: остава първото име, останалото става ***. Една дума сама става ***.';

CREATE TABLE core.person_column (
  schema_name text NOT NULL,
  table_name  text NOT NULL,
  column_name text NOT NULL,
  kind        text NOT NULL,
  shown_as    text NOT NULL,
  basis       text NOT NULL,
  decided_on  date NOT NULL,
  CONSTRAINT person_column_pk PRIMARY KEY (schema_name, table_name, column_name),
  CONSTRAINT person_column_kind_ck CHECK (kind IN ('public-office', 'public-money', 'registered', 'incidental', 'not-a-person')),
  CONSTRAINT person_column_shown_ck CHECK (shown_as IN ('full', 'masked', 'aggregate', 'absent')),
  CONSTRAINT person_column_full_ck CHECK (shown_as <> 'full' OR kind IN ('public-office', 'public-money', 'not-a-person')),
  CONSTRAINT person_column_incidental_ck CHECK (kind <> 'incidental' OR shown_as = 'absent')
);
COMMENT ON TABLE core.person_column IS 'Всяка колона, която държи или може да държи име на човек, с решението за нея. Пише я само danni, от db/persons.toml.';
COMMENT ON COLUMN core.person_column.kind IS 'В каква роля е името: public-office, public-money, registered, incidental, или not-a-person (колоната прилича на име на човек, но не е).';
COMMENT ON COLUMN core.person_column.shown_as IS 'Как стига до страниците: full, masked (първо име и ***), aggregate (само сбор, без ред), absent.';
COMMENT ON COLUMN core.person_column.basis IS 'Защо: правилото, на което стъпва решението.';

-- A column whose name says it may hold a person, wherever it is.
CREATE FUNCTION core.looks_like_person(column_name text) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT column_name ~ '(person|holder|owner|manager|receiver|beneficiar|donor|candidate|sponsor|speaker|signer|signator|contact|author|member|representative|first_name|last_name|full_name)'
     AND column_name !~ '(_kind|_id|_code|_count|_rows|_money|_eur|_share|_at|_on|_no|is_|has_)'
$$;

-- The review list: every column that holds a name by its type, or looks like one by its name, and has no decision.
CREATE VIEW core.person_column_undecided AS
SELECT n.nspname AS schema_name, c.relname AS table_name, a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS data_type
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid AND c.relkind IN ('r', 'p')
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_type t ON t.oid = a.atttypid
WHERE a.attnum > 0 AND NOT a.attisdropped
  AND n.nspname !~ '^pg_' AND n.nspname NOT IN ('information_schema', 'ops', 'public')
  AND (a.atttypid IN ('core.person_name'::regtype, 'pub.masked_name'::regtype, 'pub.role_name'::regtype)
       OR (t.typcategory = 'S' AND core.looks_like_person(a.attname)))
  AND NOT EXISTS (SELECT FROM core.person_column p
                  WHERE (p.schema_name, p.table_name, p.column_name) = (n.nspname, c.relname, a.attname));
COMMENT ON VIEW core.person_column_undecided IS 'Колони с име на човек (по тип) или с вид на такова (по име на колоната), за които няма решение. Списъкът за преглед.';

-- The serving layer gets three more ways to be wrong.
DROP VIEW core.serving_violation;
CREATE VIEW core.serving_violation AS
WITH serving AS (
  SELECT c.oid, c.relkind, n.nspname, c.relname
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE (n.nspname = 'pub' OR n.nspname LIKE 'pub\_%' OR n.nspname ~ '^api_v[0-9]+$') AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
)
SELECT s.nspname || '.' || s.relname AS relation,
       CASE WHEN t.dataset IS NULL THEN 'таблицата не е записана към набор'
            WHEN d.personal_data = 'names' THEN 'наборът съдържа имена на физически лица'
            ELSE 'наборът няма право да се показва' END AS reason
FROM serving s
LEFT JOIN core.dataset_table t ON t.schema_name = s.nspname AND t.table_name = s.relname
LEFT JOIN core.dataset d ON d.dataset = t.dataset
WHERE s.relkind IN ('r', 'p', 'f') AND s.nspname !~ '^api_v'
  AND (t.dataset IS NULL OR NOT d.is_displayable OR d.personal_data = 'names')
UNION ALL
SELECT s.nspname || '.' || s.relname, 'външен ключ извън слоя за показване: ' || k.confrelid::regclass::text
FROM serving s JOIN pg_constraint k ON k.conrelid = s.oid AND k.contype = 'f'
WHERE k.confrelid NOT IN (SELECT oid FROM serving)
UNION ALL
SELECT DISTINCT s.nspname || '.' || s.relname, 'изгледът чете извън слоя за показване: ' || d.refobjid::regclass::text
FROM serving s
JOIN pg_rewrite r ON r.ev_class = s.oid
JOIN pg_depend d ON d.classid = 'pg_rewrite'::regclass AND d.objid = r.oid AND d.refclassid = 'pg_class'::regclass AND d.refobjid <> s.oid
WHERE s.relkind IN ('v', 'm') AND d.refobjid NOT IN (SELECT oid FROM serving)
UNION ALL
SELECT s.nspname || '.' || s.relname,
       CASE WHEN a.atttypid = 'core.person_name'::regtype THEN 'пълно име на човек в слоя за показване: ' || a.attname
            WHEN p.column_name IS NULL THEN 'колона с име на човек без решение: ' || a.attname
            WHEN a.atttypid = 'pub.role_name'::regtype THEN 'пълно име, а решението за колоната не е публична роля: ' || a.attname
            WHEN a.atttypid = 'pub.masked_name'::regtype THEN 'маскирано име, а решението за колоната е друго: ' || a.attname
            ELSE 'колоната е решена като ' || p.shown_as || ', а е обикновен текст: ' || a.attname END
FROM serving s
JOIN pg_attribute a ON a.attrelid = s.oid AND a.attnum > 0 AND NOT a.attisdropped
JOIN pg_type t ON t.oid = a.atttypid
LEFT JOIN core.person_column p ON (p.schema_name, p.table_name, p.column_name) = (s.nspname, s.relname, a.attname)
WHERE s.relkind IN ('r', 'p') AND s.nspname !~ '^api_v'
  AND (a.atttypid IN ('core.person_name'::regtype, 'pub.masked_name'::regtype, 'pub.role_name'::regtype)
       OR (t.typcategory = 'S' AND core.looks_like_person(a.attname)))
  AND NOT COALESCE(   (a.atttypid = 'pub.role_name'::regtype AND p.shown_as = 'full' AND p.kind IN ('public-office', 'public-money'))
           OR (a.atttypid = 'pub.masked_name'::regtype AND p.shown_as = 'masked')
           OR (a.atttypid NOT IN ('core.person_name'::regtype, 'pub.masked_name'::regtype, 'pub.role_name'::regtype)
               AND p.kind = 'not-a-person'), false);
COMMENT ON VIEW core.serving_violation IS 'Нарушения на слоя за показване: таблица без набор с право на показване, външен ключ или изглед, който излиза извън слоя, пълно име на човек, или колона с име без решение. Трябва да е празен.';

INSERT INTO ops.check_rule (tool, name, title_bg, kind, sql, tolerance, gating) VALUES
  ('danni', 'person_columns_decided', 'Колони с име на човек или с вид на такова, за които няма решение', 'serving',
   'SELECT 0, count(*) FROM core.person_column_undecided', 0, false);

DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'pz_ingest') THEN
    GRANT SELECT ON core.person_column, core.person_column_undecided TO pz_ingest;
  END IF;
END $$;
