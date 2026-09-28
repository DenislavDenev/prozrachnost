"""What the pages read, built from live.vote after every import, for the assemblies that changed.

- First every group gets its one code (ingest/groups.py): "ГЕРБ - СДС" is "ГЕРБ-СДС", "ДПС - Ново начало" is
  "ДПС-НН" and so on, in live.vote and live.item_group, so a renamed group stays one group.
- live.line: a group's line in a vote is the choice (+, -, =) of most of its MPs who voted; a tie has no line.
  MPs outside a group (INDEPENDENT) have none.
- live.mp_stat: per MP and assembly, how often they voted, how, how often with and against their group's line
  (the group of that vote), how often present at the registration.
- The constituency and profile come from the current assembly's roster, joined by the full name only where the
  name is once in the roster and once in the roll call of the same assembly; otherwise there is none.
"""
from .groups import INDEPENDENT, aliases


def regroup(conn, assemblies):
    """The one code of every group, in the rows of these assemblies."""
    for table, key in (("live.vote", "sitting"), ("live.item_group", "sitting")):
        conn.execute(f"""UPDATE {table} t SET grp = regexp_replace(grp, '\\s*([,-])\\s*', '\\1', 'g') FROM live.sitting s
                         WHERE s.id = t.{key} AND s.assembly = ANY(%s) AND grp ~ '\\s[,-]|[,-]\\s'""", (assemblies,))
        for asm, code, group in aliases():
            conn.execute(f"""UPDATE {table} t SET grp = %s FROM live.sitting s WHERE s.id = t.{key} AND t.grp = %s
                             AND s.assembly = ANY(%s)""", (group, code, [asm] if asm in assemblies else [] if asm else assemblies))


def rebuild(conn, assemblies):
    with conn.transaction():
        regroup(conn, assemblies)
        conn.execute("DELETE FROM live.line l USING live.sitting s WHERE l.sitting = s.id AND s.assembly = ANY(%s)", (assemblies,))
        conn.execute("""
            INSERT INTO live.line (sitting, item, grp, line, voters, with_line)
            SELECT sitting, item, grp,
                   CASE WHEN y > n AND y > a THEN '+' WHEN n > y AND n > a THEN '-' WHEN a > y AND a > n THEN '=' END,
                   y + n + a,
                   CASE WHEN y > n AND y > a THEN y WHEN n > y AND n > a THEN n WHEN a > y AND a > n THEN a ELSE 0 END
            FROM (SELECT v.sitting, v.item, v.grp, count(*) FILTER (WHERE v.code = '+') y,
                         count(*) FILTER (WHERE v.code = '-') n, count(*) FILTER (WHERE v.code = '=') a
                  FROM live.vote v JOIN live.sitting s ON s.id = v.sitting
                  JOIN live.item i ON i.sitting = v.sitting AND i.no = v.item
                  WHERE i.kind = 'vote' AND s.assembly = ANY(%s) AND v.grp <> ALL(%s)
                  GROUP BY 1, 2, 3) c""", (assemblies, list(INDEPENDENT)))
        conn.execute("DELETE FROM live.mp_stat WHERE assembly = ANY(%s)", (assemblies,))
        conn.execute("""
            INSERT INTO live.mp_stat (assembly, mp, name, grp, votes, voted, yes, no_, abstain, regs, present,
                                      with_line, against_line, first, last)
            SELECT s.assembly, v.mp, m.name, (array_agg(v.grp ORDER BY s.date DESC, v.item DESC))[1],
                   count(*) FILTER (WHERE i.kind = 'vote'),
                   count(*) FILTER (WHERE i.kind = 'vote' AND v.code IN ('+', '-', '=')),
                   count(*) FILTER (WHERE v.code = '+'), count(*) FILTER (WHERE v.code = '-'), count(*) FILTER (WHERE v.code = '='),
                   count(*) FILTER (WHERE i.kind = 'registration'), count(*) FILTER (WHERE v.code = 'П'),
                   count(*) FILTER (WHERE l.line IS NOT NULL AND v.code = l.line),
                   count(*) FILTER (WHERE l.line IS NOT NULL AND v.code IN ('+', '-', '=') AND v.code <> l.line),
                   min(s.date), max(s.date)
            FROM live.vote v JOIN live.sitting s ON s.id = v.sitting
            JOIN live.item i ON i.sitting = v.sitting AND i.no = v.item
            JOIN live.mp m ON m.assembly = s.assembly AND m.no = v.mp
            LEFT JOIN live.line l ON l.sitting = v.sitting AND l.item = v.item AND l.grp = v.grp
            WHERE s.assembly = ANY(%s)
            GROUP BY s.assembly, v.mp, m.name""", (assemblies,))
        conn.execute("""
            UPDATE live.mp_stat t SET profile = r.profile, district = r.district
            FROM live.roster r
            WHERE r.assembly = t.assembly AND r.name = t.name AND t.assembly = ANY(%s)
              AND (SELECT count(*) FROM live.roster x WHERE x.assembly = r.assembly AND x.name = r.name) = 1
              AND (SELECT count(*) FROM live.mp_stat y WHERE y.assembly = t.assembly AND y.name = t.name) = 1""", (assemblies,))
