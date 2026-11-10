SELECT id, holder, company, holder_kind, holder_name, role, view, share, field_ident, entry_no, sub_uic, valid_from, valid_to, uncertain_after, observed_at, in_branch, share_pct FROM tender.edge WHERE holder_kind <> 'person'
UNION ALL
SELECT id, holder, company, holder_kind, holder_name, role, view, share, field_ident, entry_no, sub_uic, valid_from, valid_to, uncertain_after, observed_at, in_branch, share_pct FROM tender.shown_person_edge
