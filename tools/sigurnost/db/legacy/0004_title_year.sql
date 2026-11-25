-- the year in the banner of a table ("Полицейска статистика 2015" inside the set of 2016): a table whose banner names another
-- year than its set is kept in silver and left out of gold, and /sources says so
ALTER TABLE silver.resource ADD COLUMN title_year integer;

-- a numbered row the original printed twice, exactly: kept in silver, out of gold and out of the checks
ALTER TABLE silver.row ADD COLUMN dup boolean NOT NULL DEFAULT false;
