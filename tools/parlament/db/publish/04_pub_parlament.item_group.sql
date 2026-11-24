-- window: sitting IN (SELECT id FROM pub_parlament.sitting WHERE date >= %(since)s)
SELECT sitting, no, grp, yes, no_, abstain, voted, present, listed FROM parlament.item_group
