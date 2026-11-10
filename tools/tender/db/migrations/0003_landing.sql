-- The readers of Тендер land their answers through the shared module (pipeline.load): every answer is a file in
-- ops.raw_file and a load in ops.load, and every landed row says which load brought it.
-- Queues, the state of a read (tr_name_search, tr_change_day, eopsvc_check_run, eop_day, build_state) and the
-- editorial tables are not answers of a source and carry no load.
--
-- load_id is nullable: a row read before 10.11.2026 gets its load from the step `adopt`, which reads the raw
-- answers we hold once more. The rule `landed_rows_have_a_load` (db/checks.toml) counts the rows without one.

ALTER TABLE tender_src.eop_file
  ADD COLUMN rows integer,
  ADD COLUMN load_id bigint,
  ADD COLUMN raw_file_id bigint,
  ADD CONSTRAINT eop_file_load_id_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  ADD CONSTRAINT eop_file_raw_file_id_fk FOREIGN KEY (raw_file_id) REFERENCES ops.raw_file (raw_file_id);
COMMENT ON TABLE tender_src.eop_file IS 'Отворените данни на ЦАИС ЕОП: файлът на всеки ден и вид (поръчки, договори, анекси, OCDS), от който се изгражда. Това е файлът на действащото зареждане; по-малък файл чака второ четене и дотогава не го заменя.';
COMMENT ON COLUMN tender_src.eop_file.rows IS 'Брой записи във файла.';
COMMENT ON COLUMN tender_src.eop_file.load_id IS 'Зареждането (ops.load), което е приело файла.';
COMMENT ON COLUMN tender_src.eop_file.raw_file_id IS 'Файлът в регистъра на суровите файлове (ops.raw_file); изграждането го чете оттам и проверява sha256.';

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['fx_rate', 'tr_deed', 'tr_role', 'tr_person', 'eopsvc_offer', 'eopsvc_lot', 'eopsvc_contract_lot',
                           'eopsvc_announcement', 'eopsvc_detail', 'eopsvc_publication', 'eopsvc_appeal', 'eopsvc_document',
                           'sebra_resource', 'sebra_payment'] LOOP
    EXECUTE format('ALTER TABLE tender_src.%I ADD COLUMN load_id bigint, ADD CONSTRAINT %I FOREIGN KEY (load_id) REFERENCES ops.load (load_id)',
                   t, t || '_load_id_fk');
    EXECUTE format('COMMENT ON COLUMN tender_src.%I.load_id IS %L', t,
                   'Зареждането (ops.load), което последно е записало реда: от него се стига до суровия отговор и до пускането.');
  END LOOP;
END $$;

COMMENT ON TABLE tender_src.fx_rate IS 'Референтните курсове на ЕЦБ към еврото по дни, за договори във валута, различна от лев и евро. Едно зареждане държи цялата история. Минават в core.fx_rate, когато Икономика премине.';
COMMENT ON COLUMN tender_src.sebra_resource.held_sha IS 'Не се ползва от 10.11.2026: задържането е в ops.hold.';
COMMENT ON COLUMN tender_src.eopsvc_queue.held_sha IS 'Знак, че последният отговор е задържан и поръчката се чете пак след ден; самото задържане е в ops.hold.';
COMMENT ON COLUMN tender_src.tr_queue.held_sha IS 'Знак, че последният отговор е задържан (липсваща партида или по-малко редове) и партидата се чете пак след ден.';

-- Moments carry their zone (ARCHITECTURE, 5). The flat files of ЦАИС ЕОП write local time without one, and until now
-- the pages showed it as written; the values we hold are read as Sofia time.
ALTER TABLE pub_tender.tender
  ALTER COLUMN published_at TYPE timestamptz USING published_at AT TIME ZONE 'Europe/Sofia',
  ALTER COLUMN submission_deadline TYPE timestamptz USING submission_deadline AT TIME ZONE 'Europe/Sofia';
ALTER TABLE pub_tender.contract ALTER COLUMN published_at TYPE timestamptz USING published_at AT TIME ZONE 'Europe/Sofia';
ALTER TABLE pub_tender.amendment ALTER COLUMN published_at TYPE timestamptz USING published_at AT TIME ZONE 'Europe/Sofia';
ALTER TABLE pub_tender.award ALTER COLUMN published_at TYPE timestamptz USING published_at AT TIME ZONE 'Europe/Sofia';
ALTER TABLE pub_tender.notice ALTER COLUMN published_at TYPE timestamptz USING published_at AT TIME ZONE 'Europe/Sofia';
ALTER TABLE pub_tender.annex_orphan ALTER COLUMN published_at TYPE timestamptz USING published_at AT TIME ZONE 'Europe/Sofia';
