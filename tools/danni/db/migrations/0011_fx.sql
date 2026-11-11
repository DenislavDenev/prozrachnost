-- The daily exchange rates of core.fx_rate come from Икономика (the BNB archive, from 1991; the ECB reference rates
-- the BNB publishes, from 2026). Only the foundation writes core: Икономика calls this function at the end of its
-- step, and the function, owned by the foundation, reads the two views Икономика gives it (ikonomika.fx_rate,
-- ikonomika.fx_currency) and brings core.currency and core.fx_rate up to them. Returns how many rows it changed.
CREATE FUNCTION core.take_fx_rates() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE n integer := 0; m integer;
BEGIN
  IF to_regclass('ikonomika.fx_rate') IS NULL OR to_regclass('ikonomika.fx_currency') IS NULL THEN
    RETURN 0;
  END IF;
  INSERT INTO core.currency (currency_code, name_bg)
  SELECT currency_code, name_bg FROM ikonomika.fx_currency
  ON CONFLICT (currency_code) DO UPDATE SET name_bg = EXCLUDED.name_bg
  WHERE core.currency.fixed_per_eur IS NULL AND core.currency.name_bg <> EXCLUDED.name_bg;
  GET DIAGNOSTICS m = ROW_COUNT; n := n + m;
  INSERT INTO core.fx_rate (currency_code, day, per_eur, source)
  SELECT currency_code, day, per_eur, source FROM ikonomika.fx_rate
  ON CONFLICT (currency_code, day) DO UPDATE SET per_eur = EXCLUDED.per_eur, source = EXCLUDED.source
  WHERE (core.fx_rate.per_eur, core.fx_rate.source) IS DISTINCT FROM (EXCLUDED.per_eur, EXCLUDED.source);
  GET DIAGNOSTICS m = ROW_COUNT; n := n + m;
  -- a rate the BNB no longer has (a confirmed correction of its archive) leaves with it; the change is in the
  -- change log of Икономика
  DELETE FROM core.fx_rate f
  WHERE f.source IN ('bnb', 'bnb-bgn')
    AND NOT EXISTS (SELECT 1 FROM ikonomika.fx_rate x WHERE x.currency_code = f.currency_code AND x.day = f.day);
  GET DIAGNOSTICS m = ROW_COUNT; n := n + m;
  RETURN n;
END $$;
COMMENT ON FUNCTION core.take_fx_rates() IS 'Пренася курсовете на БНБ от Икономика (ikonomika.fx_rate) в core.fx_rate и валутите им в core.currency. Вика я Икономика в края на стъпката си; пише основата.';
REVOKE ALL ON FUNCTION core.take_fx_rates() FROM PUBLIC;
DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'ikonomika') THEN
    GRANT EXECUTE ON FUNCTION core.take_fx_rates() TO ikonomika;
  END IF;
END $$;

COMMENT ON COLUMN core.fx_rate.source IS 'Откъде е курсът: bnb (референтният курс на ЕЦБ, както го публикува БНБ, от 2026), bnb-bgn (курсът на БНБ в левове, превърнат с курса на еврото в левове от същия ден, до 2025).';
COMMENT ON COLUMN core.fx_rate.per_eur IS 'Колко единици от валутата са едно евро в този ден.';

