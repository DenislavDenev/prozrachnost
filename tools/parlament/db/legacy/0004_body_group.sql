-- the code of a group body (live.body, kind "група") as the roll call writes it (ingest/stats.link_bodies): its colour
-- on the MP's timeline, its leaders on the group's page
ALTER TABLE live.body ADD COLUMN grp text;
CREATE INDEX ON live.body (assembly, grp);
CREATE INDEX ON live.speech (sitting) WHERE profile IS NOT NULL;
