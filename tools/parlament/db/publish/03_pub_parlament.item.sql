-- window: sitting IN (SELECT id FROM pub_parlament.sitting WHERE date >= %(since)s)
SELECT sitting, no, kind, at, topic, yes, no_, abstain, voted, present, listed, mismatch FROM parlament.item
