-- Закони и решения, the serving layer: pub_zakoni is what the pages, the CSV and the JSON read, and nothing else.
-- It is written only by pipeline.publish (db/publish/*.sql), in one transaction, after every rule has passed. Its
-- tables have the columns of the old gold tables in the same order, so a page and its JSON did not change in the move.
DO $$ BEGIN IF to_regnamespace('pub_zakoni') IS NULL THEN CREATE SCHEMA pub_zakoni; END IF; END $$;
COMMENT ON SCHEMA pub_zakoni IS 'Закони и решения, слой за показване: актовете на МС, консултациите, стратегическите документи, промените в актовете и състоянието на източника. Оттук четат страниците.';

CREATE TABLE pub_zakoni.act (LIKE zakoni.act INCLUDING COMMENTS, CONSTRAINT act_pk PRIMARY KEY (pris_id));
CREATE INDEX act_accepted_idx ON pub_zakoni.act (accepted);
CREATE INDEX act_act_type_accepted_idx ON pub_zakoni.act (act_type, accepted);
CREATE INDEX act_consultation_reg_num_idx ON pub_zakoni.act (consultation_reg_num);
CREATE INDEX act_fts_idx ON pub_zakoni.act USING gin (to_tsvector('simple', about));
COMMENT ON TABLE pub_zakoni.act IS 'Акт на Министерския съвет (ПРИС).';

CREATE TABLE pub_zakoni.act_institution (LIKE zakoni.act_institution INCLUDING COMMENTS,
  CONSTRAINT act_institution_pk PRIMARY KEY (pris_id, institution_id),
  CONSTRAINT act_institution_pris_id_fk FOREIGN KEY (pris_id) REFERENCES pub_zakoni.act (pris_id) DEFERRABLE INITIALLY DEFERRED);
COMMENT ON TABLE pub_zakoni.act_institution IS 'Институциите на акт.';

CREATE TABLE pub_zakoni.act_relation (LIKE zakoni.act_relation INCLUDING COMMENTS,
  CONSTRAINT act_relation_pk PRIMARY KEY (pris_id, ord),
  CONSTRAINT act_relation_pris_id_fk FOREIGN KEY (pris_id) REFERENCES pub_zakoni.act (pris_id) DEFERRABLE INITIALLY DEFERRED);
COMMENT ON TABLE pub_zakoni.act_relation IS 'Свързан акт, както го посочва регистърът.';

CREATE TABLE pub_zakoni.act_change (
  change_id   bigint NOT NULL,
  pris_id     integer NOT NULL,
  detected_at timestamptz NOT NULL,
  field       text,
  old         text,
  new         text,
  cause       text NOT NULL,
  CONSTRAINT act_change_pk PRIMARY KEY (change_id)
);
CREATE INDEX act_change_pris_id_idx ON pub_zakoni.act_change (pris_id, change_id DESC);
COMMENT ON TABLE pub_zakoni.act_change IS 'Какво е променил източникът в акт след първото прочитане, поле по поле (от дневника на промените). Историята не се презаписва.';
COMMENT ON COLUMN pub_zakoni.act_change.change_id IS 'Номерът на реда в дневника.';
COMMENT ON COLUMN pub_zakoni.act_change.pris_id IS 'Актът.';
COMMENT ON COLUMN pub_zakoni.act_change.detected_at IS 'Кога е видяна промяната (часът на четенето на архива).';
COMMENT ON COLUMN pub_zakoni.act_change.field IS 'Полето.';
COMMENT ON COLUMN pub_zakoni.act_change.old IS 'Преди (до 2 000 знака).';
COMMENT ON COLUMN pub_zakoni.act_change.new IS 'След (до 2 000 знака).';
COMMENT ON COLUMN pub_zakoni.act_change.cause IS 'Видът: new-record нов запис, rewritten променено поле, removed махнат.';

CREATE TABLE pub_zakoni.consultation (LIKE zakoni.consultation INCLUDING COMMENTS, CONSTRAINT consultation_pk PRIMARY KEY (reg_num));
ALTER TABLE pub_zakoni.consultation RENAME COLUMN municipality_code TO municipality_id;
ALTER TABLE pub_zakoni.consultation ADD CONSTRAINT consultation_municipality_id_fk
  FOREIGN KEY (municipality_id) REFERENCES pub.municipality (eik) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX consultation_opened_idx ON pub_zakoni.consultation (opened);
CREATE INDEX consultation_closes_idx ON pub_zakoni.consultation (closes);
CREATE INDEX consultation_institution_id_idx ON pub_zakoni.consultation (institution_id);
CREATE INDEX consultation_municipality_id_idx ON pub_zakoni.consultation (municipality_id);
CREATE INDEX consultation_fts_idx ON pub_zakoni.consultation USING gin (to_tsvector('simple', name));
COMMENT ON TABLE pub_zakoni.consultation IS 'Обществена консултация.';
COMMENT ON COLUMN pub_zakoni.consultation.municipality_id IS 'ЕИК на общинската администрация (с него страниците адресират общината) на общинска консултация; празно, когато няма.';

CREATE TABLE pub_zakoni.consultation_file (LIKE zakoni.consultation_file INCLUDING COMMENTS,
  CONSTRAINT consultation_file_pk PRIMARY KEY (reg_num, ord),
  CONSTRAINT consultation_file_reg_num_fk FOREIGN KEY (reg_num) REFERENCES pub_zakoni.consultation (reg_num) DEFERRABLE INITIALLY DEFERRED);
COMMENT ON TABLE pub_zakoni.consultation_file IS 'Документ на консултация.';

CREATE TABLE pub_zakoni.strategy_doc (LIKE zakoni.strategy_doc INCLUDING COMMENTS, CONSTRAINT strategy_doc_pk PRIMARY KEY (doc_key));
CREATE INDEX strategy_doc_accepted_idx ON pub_zakoni.strategy_doc (accepted);
CREATE INDEX strategy_doc_fts_idx ON pub_zakoni.strategy_doc USING gin (to_tsvector('simple', name));
COMMENT ON TABLE pub_zakoni.strategy_doc IS 'Стратегически документ.';

CREATE TABLE pub_zakoni.source_state (LIKE zakoni.source_state INCLUDING COMMENTS, last_logged timestamptz,
  CONSTRAINT source_state_pk PRIMARY KEY (ref));
COMMENT ON TABLE pub_zakoni.source_state IS 'Последното четене на всеки ресурс на набора.';
COMMENT ON COLUMN pub_zakoni.source_state.last_logged IS 'Последният ред на ресурса в дневника на промените.';

CREATE TABLE pub_zakoni.reconciliation (
  name       text NOT NULL,
  checked_at timestamptz,
  expected   numeric,
  actual     numeric,
  ok         boolean,
  kind       text NOT NULL,
  note       text NOT NULL,
  CONSTRAINT reconciliation_pk PRIMARY KEY (name),
  CONSTRAINT reconciliation_kind_ck CHECK (kind IN ('block', 'known', 'info'))
);
COMMENT ON TABLE pub_zakoni.reconciliation IS 'Последният резултат на всяка сверка на броя: файл срещу нашите таблици (блокира) и другите справки на източника (известна разлика или информация).';
COMMENT ON COLUMN pub_zakoni.reconciliation.name IS 'Правилото.';
COMMENT ON COLUMN pub_zakoni.reconciliation.checked_at IS 'Кога е проверено.';
COMMENT ON COLUMN pub_zakoni.reconciliation.expected IS 'Очаквано.';
COMMENT ON COLUMN pub_zakoni.reconciliation.actual IS 'Намерено.';
COMMENT ON COLUMN pub_zakoni.reconciliation.ok IS 'Съвпада ли.';
COMMENT ON COLUMN pub_zakoni.reconciliation.kind IS 'block блокира публикуването, known известна разлика в източника, info информация.';
COMMENT ON COLUMN pub_zakoni.reconciliation.note IS 'Какво се сравнява, с думи.';
