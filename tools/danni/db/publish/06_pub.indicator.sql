SELECT i.indicator_code, i.dataset, i.title_bg, i.unit_code, u.name_bg, i.kind, i.definition_bg, i.official_code
FROM core.indicator i JOIN core.unit u USING (unit_code)
