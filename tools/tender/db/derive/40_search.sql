-- One search index over buyers, companies, persons and procedures (app/queries.py search()).
-- Every word of the query is a prefix of some word of the name, in any order ("Иван Петров" finds
-- "Иван Георгиев Петров"): tsvector 'simple' on the normalized key. Trigram similarity ranks the
-- hits and catches typos when nothing matches. Latin queries are transliterated in the app.
SET search_path = stage, public;

CREATE TABLE search_item (
  kind text NOT NULL, ref text NOT NULL, label text NOT NULL, sub text, key text NOT NULL,
  weight numeric NOT NULL DEFAULT 0,
  words tsvector GENERATED ALWAYS AS (to_tsvector('simple', key)) STORED
);

INSERT INTO search_item
SELECT 'buyer', b.eik, b.name, 'Възложител · ЕИК ' || b.eik,
       upper(translate(b.name, '"„“”''«»', '')), coalesce(s.amount_eur, 0)
FROM buyer b LEFT JOIN buyer_stats s ON s.eik = b.eik WHERE b.name IS NOT NULL;

INSERT INTO search_item
SELECT 'company', c.key, c.name,
       'Фирма' || coalesce(' · ЕИК ' || c.eik, '') || coalesce(' · ' || c.seat, ''),
       upper(translate(c.name, '"„“”''«»', '')), coalesce(s.amount_eur, 0)
FROM company c LEFT JOIN company_stats s ON s.key = c.key WHERE c.name IS NOT NULL;

INSERT INTO search_item
SELECT 'person', p.id, p.name,
       'Лице · ' || count(DISTINCT e.company) || ' дружества',
       p.name_key, count(DISTINCT e.company)
FROM person p JOIN edge e ON e.holder = 'p:' || p.id
GROUP BY p.id, p.name, p.name_key;

INSERT INTO search_item
SELECT 'tender', t.unp, coalesce(t.subject, t.unp), 'Поръчка · ' || t.unp,
       upper(coalesce(t.subject, '')) || ' ' || t.unp, 0
FROM tender t;

CREATE INDEX ON search_item USING gin (key gin_trgm_ops);
CREATE INDEX ON search_item USING gin (words);
CREATE INDEX ON search_item (kind, ref);
