"""The silver tables: name -> (key columns, data columns). db/migrations/0001_init.sql has the same columns with types;
a test compares the two. Every silver row is versioned (valid_from, valid_to) and carries the sha256 of the file it came
from; the row's own hash is taken over the data columns only."""

TABLES = {
    "pris_act": (("pris_id",), (
        "origin", "doc_num", "accepted", "about", "about_raw", "legal_act_type", "legal_reason", "importer", "protocol",
        "public_consultation_number", "gazette_number", "gazette_year_raw", "gazette_year", "version", "active",
        "published", "deleted", "confidential")),
    "pris_institution": (("pris_id", "ord"), ("institution_id", "name")),
    "pris_tag": (("pris_id", "ord"), ("tag",)),
    "pris_related": (("pris_id", "ord"), ("relation_type", "related_pris_id", "act_type", "act_name")),
    "consultation": (("reg_num",), (
        "consultation_type", "name", "description", "description_raw", "act_type", "date_open", "date_close",
        "short_term_reason", "active", "policy_area", "legislative_program_id", "operational_program_id",
        "institution_id", "institution_name", "institution_address", "proposal_ways", "law_name", "law_id", "pris_id",
        "comment_count", "comment_first", "comment_last")),
    "consultation_file": (("reg_num", "ord"), ("kind", "doc_date", "link")),
    "strategy_doc": (("doc_key",), (
        "name", "level", "policy_area", "doc_type", "act_link", "pris_act_id", "accepting_institution_type",
        "document_date", "public_consultation_number", "active", "date_accepted", "date_accepted_raw", "date_expiring",
        "date_expiring_raw")),
    "strategy_author": (("doc_key", "ord"), ("institution",)),
    "strategy_file": (("doc_key", "ord"), ("name", "path", "version")),
    "strategy_sub": (("doc_key", "ord"), (
        "sub_id", "parent_sub_id", "name", "level", "policy_area", "doc_type", "pris_act_id", "date_accepted",
        "date_expiring")),
    "impact_contract": (("ic_key",), (
        "institution_id", "institution_name", "contract_date", "price_bgn", "eik", "executor", "executor_kind",
        "subject", "description", "active")),
}

# which tables belong to one source record (children go with their parent in the hold rule)
PARENT = {"pris_institution": "pris_act", "pris_tag": "pris_act", "pris_related": "pris_act",
          "consultation_file": "consultation", "strategy_author": "strategy_doc", "strategy_file": "strategy_doc",
          "strategy_sub": "strategy_doc"}
