-- the municipality by the ЕИК of its administration: the pages address a municipality by it
SELECT c.reg_num, c.number, c.name, c.description, c.level, c.act_type, c.opened, c.closes, c.days, c.short_term,
       c.short_term_applies, c.short_term_reason, c.reason_given, c.policy_area, c.archived_area, c.institution_id,
       c.institution_name, m.eik, c.law_name, c.law_id, c.act_pris_id, c.comment_count, c.files, c.source, c.source_url,
       c.raw_sha256, c.first_seen, c.last_seen
FROM zakoni.consultation c LEFT JOIN core.municipality m USING (municipality_code)
