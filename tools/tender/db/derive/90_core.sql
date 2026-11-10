-- The links to the shared dimensions, and the descriptions of the tables. Last on purpose: adding a foreign key
-- takes a lock on core.org and core.municipality until the build commits, and this way that is moments, not the
-- half hour of the whole build.
--
-- An organisation whose ЕИК is not in core.org keeps org_eik empty (it is not thrown away and gets no invented
-- key); the rule `org_unmatched` counts them. core.org itself is filled from these tables by the step `org` of the
-- shared dimensions, so a company seen today is linked from the next build on.
SET search_path = tender_stage, public;

ALTER TABLE company ADD COLUMN org_eik text;
UPDATE company c SET org_eik = o.eik FROM core.org o WHERE o.eik = c.eik;
ALTER TABLE company ADD CONSTRAINT company_org_eik_fk FOREIGN KEY (org_eik) REFERENCES core.org (eik);

ALTER TABLE buyer ADD COLUMN org_eik text;
UPDATE buyer b SET org_eik = o.eik FROM core.org o WHERE o.eik = b.eik;
ALTER TABLE buyer ADD CONSTRAINT buyer_org_eik_fk FOREIGN KEY (org_eik) REFERENCES core.org (eik);

ALTER TABLE buyer_place ADD CONSTRAINT buyer_place_municipality_code_fk FOREIGN KEY (municipality_code)
  REFERENCES core.municipality (municipality_code);

-- the shared dimensions read the organisations from here (their own role, once it no longer reads every table)
DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'danni') THEN
    GRANT SELECT ON company, buyer TO danni;
  END IF;
END $$;

COMMENT ON TABLE buyer IS 'Възложителите по ЕИК, с името и адреса от отворените данни на ЦАИС ЕОП.';
COMMENT ON COLUMN buyer.org_eik IS 'Същият ЕИК, когато организацията е в core.org; празно, докато не влезе там.';
COMMENT ON TABLE tender IS 'Поръчките по УНП: предмет, процедура, прогнозна стойност, срок и състояние.';
COMMENT ON TABLE lot IS 'Обособените позиции на поръчките от отворените данни.';
COMMENT ON TABLE contract IS 'Договорите: възложител, изпълнител, дати, стойности в оригиналната валута и в евро, брой оферти.';
COMMENT ON TABLE contract_supplier IS 'Изпълнителите по всеки договор; обединение е по един ред за всеки член.';
COMMENT ON TABLE amendment IS 'Анексите към договорите с новата стойност и причината.';
COMMENT ON TABLE subcontract IS 'Подизпълнителите, посочени в договора.';
COMMENT ON TABLE source_record IS 'Къде в суровите файлове е всеки публикуван запис на договор или поръчка (ден, файл, пореден номер).';
COMMENT ON TABLE award IS 'Обявления за възложена поръчка без възлагане: позиция, дата, брой оферти.';
COMMENT ON TABLE notice IS 'Обявленията от отворените данни с номера и вида им.';
COMMENT ON TABLE annex_orphan IS 'Анекси, чийто договор не може да се определи еднозначно.';
COMMENT ON TABLE company IS 'Фирмите: изпълнители, участници в оферти и дружества от Търговския регистър, по ключ eik:<ЕИК> или name:<име>.';
COMMENT ON COLUMN company.org_eik IS 'Същият ЕИК, когато организацията е в core.org; празно, докато не влезе там.';
COMMENT ON TABLE company_stats IS 'Обобщение по фирма: брой договори, стойност, възложители, първи и последен договор.';
COMMENT ON TABLE buyer_stats IS 'Обобщение по възложител: договори, стойност, изпълнители, поръчки.';
COMMENT ON TABLE tender_stats IS 'Обобщение по поръчка: договори, стойност, договори с една оферта.';
COMMENT ON TABLE person IS 'Лицата, които Търговският регистър различава по свой идентификатор. Само в приемната страна; страниците показват част от тях (shown_person).';
COMMENT ON TABLE edge IS 'Връзките в регистъра: кой каква роля има в кое дружество и кога. Всички роли; страниците показват част от тях.';
COMMENT ON TABLE node IS 'Възлите на мрежата (лице, дружество, чуждо лице) с името им.';
COMMENT ON TABLE node_contract IS 'Договорите на всяко дружество като възел на мрежата.';
COMMENT ON TABLE person_stats IS 'Обобщение по лице върху всички му роли. Само в приемната страна.';
COMMENT ON TABLE network_component IS 'Свързаните компоненти на мрежата по изглед и режим.';
COMMENT ON TABLE network_component_stats IS 'Размер и договори на всяка компонента.';
COMMENT ON TABLE tag_def IS 'Автоматичните отбелязвания: формула, прозорец, праг.';
COMMENT ON TABLE tag IS 'Отбелязванията по фирма с числата зад тях. Индикатор, не обвинение.';
COMMENT ON TABLE search_item IS 'Индексът за търсене по име, ЕИК и УНП върху възложители, фирми, лица и поръчки.';
COMMENT ON TABLE buyer_place IS 'Възложителите, поставени в община: самата община или нейно звено, или по адрес.';
COMMENT ON COLUMN buyer_place.municipality IS 'ЕИК на общината, в чийто контур се рисува възложителят.';
COMMENT ON COLUMN buyer_place.municipality_code IS 'Кодът на същата община по НСИ, ключ към core.municipality.';
COMMENT ON TABLE form_type IS 'Името на всеки вид обявление в ЦАИС ЕОП, научено от отворените данни.';
COMMENT ON TABLE contract_lot IS 'Позицията на всеки договор, както я номерира ЦАИС ЕОП.';
COMMENT ON TABLE offer_lot IS 'Позициите на поръчките, както ги номерира ЦАИС ЕОП, с прогнозната стойност и състоянието.';
COMMENT ON TABLE offer IS 'Офертите по поръчка и позиция, с цената в евро, фирмата и дали е спечелила.';
COMMENT ON TABLE payment IS 'Плащанията от бюджета (СЕБРА), съпоставени с фирма и възложител, когато името е еднозначно.';
COMMENT ON TABLE public_money IS 'Дружествата, получили публични пари: с договор по обществена поръчка или с плащане от СЕБРА.';
COMMENT ON TABLE person_token IS 'За всяко лице, което се показва: случайният знак, с който страниците го адресират до следващото изграждане.';
COMMENT ON TABLE shown_person_edge IS 'Ролите на лица, които се показват: само в дружества с публични пари.';
COMMENT ON TABLE shown_person IS 'Лицата, които се показват, под случайния си знак, с обхвата на търсенето по име.';
COMMENT ON TABLE shown_person_stats IS 'Обобщение по показвано лице, само върху показваните му роли.';
COMMENT ON TABLE shown_person_node IS 'Възлите на показваните лица в мрежата.';
COMMENT ON TABLE shown_person_search IS 'Редовете на показваните лица в търсенето.';
