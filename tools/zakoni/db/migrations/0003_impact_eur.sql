-- From version 5 (12.11.2026) the report of contracts for impact assessments gives the price in euro too: the source's
-- own conversion, kept as given. The clean table takes it when the source gives it, else converts by the fixed rate.
ALTER TABLE zakoni_src.impact_contract ADD COLUMN price_eur numeric(14, 2);
COMMENT ON COLUMN zakoni_src.impact_contract.price_eur IS 'Цената в евро, както я дава източникът (от версия 5 на справката); празно в по-старите версии.';
COMMENT ON COLUMN zakoni.impact_contract.price_eur IS 'Цената в евро: както я дава източникът, а когато не я дава, цената в лева по фиксирания курс 1,95583.';
