-- One search index over buyers, companies, persons and procedures. Matching is trigram on a
-- normalized key (upper case, no quotes) plus the Latin->Cyrillic transliteration of the query
-- done in the app (app/translit.py), so "Ivanov" finds "Иванов".
SET search_path = stage, public;

CREATE TABLE search_item (
  kind text NOT NULL, ref text NOT NULL, label text NOT NULL, sub text, key text NOT NULL,
  weight numeric NOT NULL DEFAULT 0
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
CREATE INDEX ON search_item (kind, ref);
