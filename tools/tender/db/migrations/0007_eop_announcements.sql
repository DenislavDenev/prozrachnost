-- Clarifications and messages of a procedure as ЦАИС ЕОП lists them on its page ("Разяснения и съобщения":
-- clarifications, committee protocols, reports, the decision on the contractor), from
-- GetPublicTenderAnnouncementsByTenderId, read with the offers. Title and time only: the documents
-- themselves stay in ЦАИС ЕОП.
CREATE TABLE eopsvc.announcement (
  tender_id bigint NOT NULL,
  id bigint NOT NULL,
  created_at timestamptz,
  title text NOT NULL,
  PRIMARY KEY (tender_id, id)
);
