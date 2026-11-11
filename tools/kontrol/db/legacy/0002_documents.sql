CREATE TABLE live.document (ref text PRIMARY KEY, payload jsonb NOT NULL, read_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE ops.document_version (ref text NOT NULL, sha256 text NOT NULL, url text NOT NULL, payload jsonb NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(ref,sha256));
