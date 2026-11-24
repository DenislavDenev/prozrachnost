-- window: sitting IN (SELECT id FROM pub_parlament.sitting WHERE date >= %(since)s)
SELECT sitting, no, role, name::pub.role_name, note, grp, profile, text FROM parlament.speech
