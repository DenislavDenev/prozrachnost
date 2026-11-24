CREATE TABLE ops.raw_read(id bigserial PRIMARY KEY,job_id bigint,source text NOT NULL,url text NOT NULL,method text NOT NULL,mode text NOT NULL CHECK(mode IN ('live','archive_reuse')),sha256 text NOT NULL,bytes bigint NOT NULL,observed_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX raw_read_job ON ops.raw_read(job_id,source,mode);
