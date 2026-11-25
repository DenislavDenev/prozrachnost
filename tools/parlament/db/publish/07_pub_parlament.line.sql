-- window: sitting IN (SELECT id FROM pub_parlament.sitting WHERE date >= %(since)s)
SELECT sitting, item, grp, line, voters, with_line FROM parlament.line
