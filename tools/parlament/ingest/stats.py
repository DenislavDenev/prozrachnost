"""What the pages read, built from live.vote after every import, for the assemblies that changed.

- First every group gets its one code (ingest/groups.py): "ГЕРБ - СДС" is "ГЕРБ-СДС", "ДПС - Ново начало" is
  "ДПС-НН" and so on, in live.vote and live.item_group, so a renamed group stays one group.
- live.line: a group's line in a vote is the side of most of its MPs who voted: + for, or - not for (against or
  abstained: a decision needs more than half of the MPs present, so an abstention does not support it either); a
  tie has no line. MPs outside a group (INDEPENDENT) have none.
- live.mp_stat: per MP and assembly, how often they voted, how, how often with and against their group's line
  (the group of that vote), how often present at the registration.
- The roll call's MP gets the Assembly's profile (live.profile) where the full name is once among the assembly's
  profiles and once in its roll call; the constituency comes from that profile, else from the current assembly's
  roster by the same rule; otherwise there is none.
- A speaker of the stenogram gets the profile where their name, as the stenogram writes it (full, or first and last),
  is one profile's in that assembly; ministers and guests are not linked (a namesake MP is not them).
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
                   CASE WHEN y > n + a THEN '+' WHEN n + a > y THEN '-' END,
                   y + n + a,
                   CASE WHEN y > n + a THEN y WHEN n + a > y THEN n + a ELSE 0 END
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
                   count(*) FILTER (WHERE l.line IS NOT NULL AND v.code IN ('+', '-', '=') AND (v.code = '+') = (l.line = '+')),
                   count(*) FILTER (WHERE l.line IS NOT NULL AND v.code IN ('+', '-', '=') AND (v.code = '+') <> (l.line = '+')),
                   min(s.date), max(s.date)
            FROM live.vote v JOIN live.sitting s ON s.id = v.sitting
            JOIN live.item i ON i.sitting = v.sitting AND i.no = v.item
            JOIN live.mp m ON m.assembly = s.assembly AND m.no = v.mp
            LEFT JOIN live.line l ON l.sitting = v.sitting AND l.item = v.item AND l.grp = v.grp
            WHERE s.assembly = ANY(%s)
            GROUP BY s.assembly, v.mp, m.name""", (assemblies,))
        link_mps(conn, assemblies)
        conn.execute("""UPDATE live.mp_stat t SET profile = m.profile, district = p.district FROM live.mp m
                        JOIN live.profile p ON p.id = m.profile
                        WHERE m.assembly = t.assembly AND m.no = t.mp AND t.assembly = ANY(%s)""", (assemblies,))
        conn.execute("""
            UPDATE live.mp_stat t SET profile = coalesce(t.profile, r.profile), district = coalesce(t.district, r.district)
            FROM live.roster r
            WHERE r.assembly = t.assembly AND r.name = t.name AND t.assembly = ANY(%s)
              AND (SELECT count(*) FROM live.roster x WHERE x.assembly = r.assembly AND x.name = r.name) = 1
              AND (SELECT count(*) FROM live.mp_stat y WHERE y.assembly = t.assembly AND y.name = t.name) = 1""", (assemblies,))


def link_mps(conn, assemblies):
    """live.mp.profile: the profile with the same full name, where the name is once among the assembly's profiles and
    once among its roll call's MPs."""
    conn.execute("""UPDATE live.mp m SET profile = u.id FROM
                      (SELECT assembly, name, min(id) id FROM live.profile WHERE assembly = ANY(%s) GROUP BY 1, 2 HAVING count(*) = 1) u
                    WHERE u.assembly = m.assembly AND u.name = m.name AND m.profile IS DISTINCT FROM u.id
                      AND (SELECT count(*) FROM live.mp x WHERE x.assembly = m.assembly AND x.name = m.name) = 1""", (assemblies,))


# the roles an MP speaks in; a minister or a guest with an MP's name is not that MP
MP_ROLES = ("председател", "председателят", "заместник-председател", "заместник председател", "докладчик", "секретар")


def link_speakers(conn, sitting=None, assemblies=None):
    """live.speech.profile, for one sitting or for assemblies: the name as the stenogram writes it (the full name, or
    the first and the last) is one profile's in the sitting's assembly."""
    where, args = ("s.id = %s", [sitting]) if sitting else ("s.assembly = ANY(%s)", [assemblies])
    conn.execute(f"""
        WITH keys AS (
            SELECT p.assembly, k.key, min(p.id) id FROM live.profile p,
                   LATERAL (VALUES (p.name), (split_part(p.name, ' ', 1) || ' ' || regexp_replace(p.name, '^.* ', ''))) k(key)
            WHERE p.assembly IN (SELECT DISTINCT s.assembly FROM live.sitting s WHERE {where})
            GROUP BY 1, 2 HAVING count(DISTINCT p.id) = 1)
        UPDATE live.speech sp SET profile = keys.id FROM live.sitting s, keys
        WHERE s.id = sp.sitting AND {where} AND keys.assembly = s.assembly AND keys.key = sp.name
          AND (sp.role IS NULL OR sp.role = ANY(%s)) AND sp.profile IS DISTINCT FROM keys.id""", (*args, *args, list(MP_ROLES)))


def link_bodies(conn):
    """live.body.grp of every group body: the group its members were in at the registrations of their time in it, the
    most common one. Only where the roll call exists (07.2009-); older groups keep their name only."""
    conn.execute("""
        UPDATE live.body b SET grp = x.grp FROM (
            SELECT DISTINCT ON (ms.body) ms.body, v.grp
            FROM live.membership ms JOIN live.body bb ON bb.id = ms.body
            JOIN live.mp m ON m.profile = ms.profile AND m.assembly = bb.assembly
            JOIN live.sitting s ON s.assembly = m.assembly
                 AND s.date BETWEEN coalesce(ms.since, s.date) AND coalesce(ms.until, s.date)
            JOIN live.item i ON i.sitting = s.id AND i.kind = 'registration'
            JOIN live.vote v ON v.sitting = i.sitting AND v.item = i.no AND v.mp = m.no
            WHERE ms.body_kind = 2
            GROUP BY ms.body, v.grp ORDER BY ms.body, count(*) DESC) x
        WHERE x.body = b.id AND b.grp IS DISTINCT FROM x.grp""")
