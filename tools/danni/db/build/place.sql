-- core.nuts, core.oblast, core.municipality and core.settlement from the current loads of EKATTE and of the
-- municipality reference. Runs in one transaction. Nothing is deleted: what left the source gets gone_at.
INSERT INTO core.nuts (nuts_code, level, name_bg, name_en, parent_code)
VALUES ('BG', 0, 'България', 'Bulgaria', NULL)
ON CONFLICT (nuts_code) DO NOTHING;

INSERT INTO core.nuts (nuts_code, level, name_bg, name_en, parent_code)
SELECT r.region, length(r.region) - 2, r.name, r.name_en, left(r.region, length(r.region) - 1)
FROM danni_src.ekatte_region r JOIN ops.load l USING (load_id)
WHERE l.is_current
ORDER BY length(r.region), r.region
ON CONFLICT (nuts_code) DO UPDATE SET name_bg = EXCLUDED.name_bg, name_en = EXCLUDED.name_en, parent_code = EXCLUDED.parent_code
WHERE (core.nuts.name_bg, core.nuts.name_en, core.nuts.parent_code) IS DISTINCT FROM (EXCLUDED.name_bg, EXCLUDED.name_en, EXCLUDED.parent_code);

INSERT INTO core.nuts (nuts_code, level, name_bg, name_en, parent_code)
SELECT o.nuts3, 3, o.name, o.name_en, o.nuts2
FROM danni_src.ekatte_oblast o JOIN ops.load l USING (load_id)
WHERE l.is_current
ORDER BY o.nuts3
ON CONFLICT (nuts_code) DO UPDATE SET name_bg = EXCLUDED.name_bg, name_en = EXCLUDED.name_en, parent_code = EXCLUDED.parent_code
WHERE (core.nuts.name_bg, core.nuts.name_en, core.nuts.parent_code) IS DISTINCT FROM (EXCLUDED.name_bg, EXCLUDED.name_en, EXCLUDED.parent_code);

INSERT INTO core.oblast (oblast_code, name_bg, name_en, nuts3_code)
SELECT o.oblast, o.name, o.name_en, o.nuts3
FROM danni_src.ekatte_oblast o JOIN ops.load l USING (load_id)
WHERE l.is_current
ORDER BY o.oblast
ON CONFLICT (oblast_code) DO UPDATE SET name_bg = EXCLUDED.name_bg, name_en = EXCLUDED.name_en, nuts3_code = EXCLUDED.nuts3_code
WHERE (core.oblast.name_bg, core.oblast.name_en, core.oblast.nuts3_code) IS DISTINCT FROM (EXCLUDED.name_bg, EXCLUDED.name_en, EXCLUDED.nuts3_code);

-- a municipality enters only with both codes: the one of EKATTE and the ЕИК from the reference
INSERT INTO core.municipality (municipality_code, oblast_code, name_bg, name_en, eik, drawn_as_eik, center_ekatte)
SELECT m.obshtina, left(m.obshtina, 3), r.name_bg, r.name_en, r.eik, r.drawn_as_eik, m.ekatte
FROM danni_src.ekatte_municipality m JOIN ops.load l USING (load_id)
JOIN danni_src.municipality_ref r ON r.municipality_code = m.obshtina
JOIN ops.load lr ON lr.load_id = r.load_id AND lr.is_current
WHERE l.is_current
ORDER BY m.obshtina
ON CONFLICT (municipality_code) DO UPDATE SET oblast_code = EXCLUDED.oblast_code, name_bg = EXCLUDED.name_bg,
  name_en = EXCLUDED.name_en, eik = EXCLUDED.eik, drawn_as_eik = EXCLUDED.drawn_as_eik,
  center_ekatte = EXCLUDED.center_ekatte, gone_at = NULL
WHERE (core.municipality.oblast_code, core.municipality.name_bg, core.municipality.name_en, core.municipality.eik,
       core.municipality.drawn_as_eik, core.municipality.center_ekatte, core.municipality.gone_at)
      IS DISTINCT FROM (EXCLUDED.oblast_code, EXCLUDED.name_bg, EXCLUDED.name_en, EXCLUDED.eik, EXCLUDED.drawn_as_eik,
                        EXCLUDED.center_ekatte, NULL::timestamptz);

UPDATE core.municipality c SET gone_at = now()
WHERE c.gone_at IS NULL AND NOT EXISTS (
  SELECT FROM danni_src.ekatte_municipality m JOIN ops.load l USING (load_id)
  WHERE l.is_current AND m.obshtina = c.municipality_code);

INSERT INTO core.settlement (ekatte, kind_prefix, name_bg, name_en, municipality_code, kmetstvo_code, settlement_kind, category)
SELECT s.ekatte, s.t_v_m, s.name, s.name_en, s.obshtina, s.kmetstvo, s.kind, s.category
FROM danni_src.ekatte_settlement s JOIN ops.load l USING (load_id)
WHERE l.is_current
ORDER BY s.ekatte
ON CONFLICT (ekatte) DO UPDATE SET kind_prefix = EXCLUDED.kind_prefix, name_bg = EXCLUDED.name_bg, name_en = EXCLUDED.name_en,
  municipality_code = EXCLUDED.municipality_code, kmetstvo_code = EXCLUDED.kmetstvo_code,
  settlement_kind = EXCLUDED.settlement_kind, category = EXCLUDED.category, gone_at = NULL
WHERE (core.settlement.kind_prefix, core.settlement.name_bg, core.settlement.name_en, core.settlement.municipality_code,
       core.settlement.kmetstvo_code, core.settlement.settlement_kind, core.settlement.category, core.settlement.gone_at)
      IS DISTINCT FROM (EXCLUDED.kind_prefix, EXCLUDED.name_bg, EXCLUDED.name_en, EXCLUDED.municipality_code,
                        EXCLUDED.kmetstvo_code, EXCLUDED.settlement_kind, EXCLUDED.category, NULL::timestamptz);

UPDATE core.settlement c SET gone_at = now()
WHERE c.gone_at IS NULL AND NOT EXISTS (
  SELECT FROM danni_src.ekatte_settlement s JOIN ops.load l USING (load_id)
  WHERE l.is_current AND s.ekatte = c.ekatte);
