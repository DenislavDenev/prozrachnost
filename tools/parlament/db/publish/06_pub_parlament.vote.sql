-- window: sitting IN (SELECT id FROM pub_parlament.sitting WHERE date >= %(since)s)
SELECT sitting, item, mp, grp, code FROM parlament.vote
