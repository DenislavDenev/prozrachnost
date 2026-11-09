-- The landing zone of the shared dimensions: EKATTE as НСИ publishes it, the municipality reference, and the
-- organisations as Тендер holds them. core.* is built from the current loads of these tables.
DO $$ BEGIN IF to_regnamespace('danni_src') IS NULL THEN CREATE SCHEMA danni_src; END IF; END $$;
COMMENT ON SCHEMA danni_src IS 'Приемна зона на общите измерения: редовете, както са в източника.';

CREATE TABLE danni_src.ekatte_region (
  load_id bigint NOT NULL, region text NOT NULL, name text NOT NULL, name_en text,
  CONSTRAINT ekatte_region_pk PRIMARY KEY (load_id, region),
  CONSTRAINT ekatte_region_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE danni_src.ekatte_region IS 'ЕКАТТЕ, ek_reg1.json и ek_reg2.json: статистическите зони и райони.';

CREATE TABLE danni_src.ekatte_oblast (
  load_id bigint NOT NULL, oblast text NOT NULL, ekatte text NOT NULL, name text NOT NULL, name_en text,
  nuts3 text NOT NULL, nuts2 text NOT NULL, nuts1 text NOT NULL,
  CONSTRAINT ekatte_oblast_pk PRIMARY KEY (load_id, oblast),
  CONSTRAINT ekatte_oblast_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE danni_src.ekatte_oblast IS 'ЕКАТТЕ, ek_obl.json: областите.';

CREATE TABLE danni_src.ekatte_municipality (
  load_id bigint NOT NULL, obshtina text NOT NULL, ekatte text NOT NULL, name text NOT NULL, name_en text,
  nuts3 text NOT NULL, category smallint,
  CONSTRAINT ekatte_municipality_pk PRIMARY KEY (load_id, obshtina),
  CONSTRAINT ekatte_municipality_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE danni_src.ekatte_municipality IS 'ЕКАТТЕ, ek_obst.json: общините с центъра им.';

CREATE TABLE danni_src.ekatte_settlement (
  load_id bigint NOT NULL, ekatte text NOT NULL, t_v_m text NOT NULL, name text NOT NULL, name_en text,
  oblast text NOT NULL, obshtina text NOT NULL, kmetstvo text, kind smallint, category smallint,
  altitude smallint, document integer, nuts3 text NOT NULL,
  CONSTRAINT ekatte_settlement_pk PRIMARY KEY (load_id, ekatte),
  CONSTRAINT ekatte_settlement_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE danni_src.ekatte_settlement IS 'ЕКАТТЕ, ek_atte.json: населените места.';

CREATE TABLE danni_src.municipality_ref (
  load_id bigint NOT NULL, municipality_code text NOT NULL, eik text NOT NULL, name_bg text NOT NULL,
  name_en text, drawn_as_eik text NOT NULL,
  CONSTRAINT municipality_ref_pk PRIMARY KEY (load_id, municipality_code),
  CONSTRAINT municipality_ref_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE danni_src.municipality_ref IS 'db/ref/municipality.csv: кодът на общината по НСИ срещу ЕИК на администрацията.';

CREATE TABLE danni_src.org_tender (
  load_id bigint NOT NULL, eik text NOT NULL, name text NOT NULL, legal_form text, seat text,
  registered_on date, register_status text, is_public_body boolean NOT NULL, is_buyer boolean NOT NULL,
  CONSTRAINT org_tender_pk PRIMARY KEY (load_id, eik),
  CONSTRAINT org_tender_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE danni_src.org_tender IS 'Юридическите лица с валиден ЕИК, както ги държи Тендер (Търговският регистър и възложителите).';

DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'pz_web') THEN
    GRANT USAGE ON SCHEMA danni_src TO pz_web;
    GRANT SELECT ON ALL TABLES IN SCHEMA danni_src TO pz_web;
  END IF;
END $$;
