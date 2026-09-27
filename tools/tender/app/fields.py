"""Labels for the fields of the raw ЦАИС ЕОП records, so a tender or contract page can show every
published field. Unknown fields are still shown, under their original name."""

LABEL = {
    # procedure
    "uniqueProcurementNumber": "УНП", "tenderId": "Номер в ЦАИС ЕОП", "noticeId": "Номер на обявлението",
    "noticeType": "Вид обявление", "publicationDate": "Публикувано", "procedureType": "Вид процедура",
    "subject": "Предмет", "tenderName": "Предмет на поръчката", "lotTenderName": "Предмет на позицията",
    "mainCpvCode": "Основен CPV код", "mainCpvDescription": "CPV описание", "tenderMainCpv": "Основен CPV код",
    "tenderMainCpvDescription": "CPV описание", "typeOfContract": "Вид на поръчката",
    "estimatedValue": "Прогнозна стойност", "currency": "Валута", "legalBasis": "Правно основание",
    "awardMethod": "Критерий за възлагане", "hasJointProcurement": "Съвместна поръчка",
    "isJointProcurement": "Съвместна поръчка", "isCentralPurchasingAuthority": "Централен орган за покупки",
    "buyerName": "Възложител", "buyerRegistryNumber": "ЕИК на възложителя", "buyerType": "Вид възложител",
    "buyerMainActivity": "Основна дейност на възложителя", "submissionDeadline": "Срок за оферти",
    "lotIdentifier": "Обособена позиция", "lotsCount": "Брой обособени позиции", "isLot": "Запис за позиция",
    "isEuFunded": "Финансиране от ЕС", "europeanProgram": "Европейска програма",
    "hasUnsecuredFunding": "Необезпечено финансиране", "isFrameworkAgreement": "Рамково споразумение",
    "isDpsProcedure": "Динамична система за покупки", "isAcceleratedProcedure": "Ускорена процедура",
    "hasElectronicAuction": "Електронен търг", "isStrategicProcurement": "Стратегическа поръчка",
    "isStrategicTender": "Стратегическа поръчка", "isGreenProcurement": "Зелена поръчка",
    "isSocialProcurement": "Социална поръчка", "isInnovationProcurement": "Иновативна поръчка",
    "hasOptions": "Опции", "hasRenewal": "Подновяване", "isReservedProcurement": "Запазена поръчка",
    "hasVariants": "Варианти", "executionPlaceNuts": "Място на изпълнение (NUTS)",
    "tenderDuration": "Срок на изпълнение", "tenderDurationUnit": "Единица за срока",
    "tenderStartDate": "Начало на изпълнението", "tenderEndDate": "Край на изпълнението",
    "electronicInvoicing": "Електронно фактуриране", "electronicPayment": "Електронно плащане",
    "electronicOrdering": "Електронни поръчки", "changeNoticeCount": "Брой обявления за промяна",
    "isCancelled": "Прекратена (поле на обявлението; прекратяването с решение е в хронологията)", "changeNoticeDocuments": "Документи за промени", "linkToOjEu": "Връзка към ОВ на ЕС",
    # contract
    "contractNumber": "Номер на договора", "contractDate": "Дата на сключване", "contractValue": "Стойност на договора",
    "contractCurrency": "Валута на договора", "contractSubject": "Предмет на договора",
    "awardedToGroup": "Възложен на обединение", "supplierRegisterNumber": "ЕИК на изпълнителя",
    "supplierName": "Изпълнител", "supplierNutsCode": "Седалище на изпълнителя (NUTS)",
    "supplierNationality": "Националност на изпълнителя", "supplierCompanySizeCode": "Размер на изпълнителя",
    "hasSubcontractors": "Подизпълнители", "subcontractorName": "Подизпълнител",
    "subcontractorRegistryNumber": "ЕИК на подизпълнителя", "subcontractingPercent": "Дял на подизпълнението, %",
    "subcontractingAmount": "Стойност на подизпълнението", "frameworkAgreementContract": "Договор по рамково споразумение",
    "linkedTenders": "Свързани поръчки", "contractUnderQs": "По квалификационна система",
    "hasAuctionQuotationMethod": "Търг или котировки", "isExceptionContract": "Изключение от ЗОП",
    "directAwardJustification": "Основание за пряко възлагане", "offersCount": "Брой оферти",
    "smeOffersCount": "Оферти от МСП", "disqualifiedOffersCount": "Отстранени оферти",
    "noEeaOffersCount": "Оферти извън ЕИП", "contractPeriod": "Срок на договора, дни",
    "noAwarding": "Без възлагане",
    # annex
    "lastContractValue": "Стойност преди изменението", "currentContractValue": "Стойност след изменението",
    "contractValueDifference": "Разлика", "changeDescription": "Описание на изменението",
    "changeReason": "Основание за изменението",
}

# money fields and the field that holds their currency
MONEY = {"estimatedValue": "currency", "contractValue": "contractCurrency", "lastContractValue": "contractCurrency",
         "currentContractValue": "contractCurrency", "contractValueDifference": "contractCurrency",
         "subcontractingAmount": "contractCurrency"}
# shown elsewhere on the page or not useful on their own
SKIP = {"currency", "contractCurrency"}


def fields(rec):
    """[(label, key, value, currency)] for every non-empty field of a raw record, in source order."""
    out = []
    for k, v in rec.items():
        if k in SKIP or v in (None, "", [], {}):
            continue
        if isinstance(v, (dict, list)):
            continue
        out.append((LABEL.get(k, k), k, v, rec.get(MONEY[k]) if k in MONEY else None))
    return out


def ocds_fields(rel):
    """Flattened OCDS tender part (description, lots, items, periods, documents), without contact
    persons. [(path, value)] with dotted paths."""
    out = []

    def walk(prefix, v):
        if isinstance(v, dict):
            for k, x in v.items():
                if k in ("contactPoint",):
                    continue
                walk(f"{prefix}.{k}" if prefix else k, x)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(f"{prefix}[{i + 1}]", x)
        elif v not in (None, ""):
            out.append((prefix, v))

    walk("", {k: rel.get(k) for k in ("tender", "awards", "contracts", "bids") if rel.get(k)})
    return out
