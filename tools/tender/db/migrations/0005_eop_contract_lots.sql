-- The lot of each contract and the lot titles as ЦАИС ЕОП numbers them (GetPublishedContractListItems,
-- GetPublishedLots). The open data's lotIdentifier on contracts does not always follow this numbering
-- (e.g. 00282-2026-0008: a contract of lot 6 comes as LOT-0001), so offers are tied to contracts
-- through these rows; the contract id here is the contract number of the open data.
CREATE TABLE eopsvc.contract_lot (
  tender_id bigint NOT NULL,
  lot_no int NOT NULL,                  -- 0: the procedure has no lots
  contract_id text NOT NULL,
  PRIMARY KEY (tender_id, contract_id)
);

CREATE TABLE eopsvc.lot (
  tender_id bigint NOT NULL,
  lot_no int NOT NULL,
  title text,
  PRIMARY KEY (tender_id, lot_no)
);
