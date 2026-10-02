-- the hyperlink resources of a set (the monthly bulletin is 104 links to PDF files on mvr.bg, not tables): kept as links with
-- their month, never opened; the portal's own metadata, read from the archive's list of the set
CREATE TABLE silver.link (
  resource_uri text PRIMARY KEY, set_uri text NOT NULL REFERENCES silver.dataset, name text NOT NULL, url text NOT NULL,
  period date,                      -- first day of the month the bulletin is for, from its name
  source_updated_at timestamptz, list_sha256 text NOT NULL);
CREATE INDEX link_set ON silver.link (set_uri, period);

-- a first build read the 104 links as empty tables: those rows are removed (the links are kept above)
DELETE FROM silver.resource WHERE set_uri IN (SELECT set_uri FROM silver.dataset WHERE kind = 'bulletin');
