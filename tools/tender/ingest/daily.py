"""The D-1 chain: yesterday's data on the site the morning it is published.

ЦАИС ЕОП publishes day D at about 06:00 UTC on D+1. n8n calls this every 15 minutes from 06:05 UTC;
until the day is there it only says it is waiting, and once a day has been done it says so. When the day
is there, in order:

  1. build            eop (the new day) -> fx -> normalize -> derive -> publish
  2. queue            new ЕИК for the register (tr-seed), new and changed procedures for the offers
  3. offers           the validity check, then the offers of the new and changed procedures and of the
                      pages visitors opened (a running backfill reads them first; else read here)
  4. register         the new ЕИК and the partidas the change list named (the same way)
  5. build            again, so the offers and companies read in 3-4 are on the site
  6. freshness        every source where it should be (below); a failure is an alert

The waits have a budget: what is not read by then is read later and shows at the evening build.
"""
import datetime as dt
import time

from . import build, db, eop, eop_offers, tr_worker

OFFERS_BUDGET = 20 * 60
TR_BUDGET = 20 * 60
FINAL_UTC = "10:05"  # the last call of the morning (n8n); after it a missing day is an alert


def yesterday():
    return str(dt.date.today() - dt.timedelta(days=1))


def done(conn, day):
    return conn.execute("""SELECT 1 FROM ops.job_run WHERE step = 'daily' AND status = 'ok' AND inputs->>'day' = %s""",
                        (day,)).fetchone() is not None


def build_all(out, steps=("eop", "fx", "normalize", "derive", "publish")):
    for name in steps:
        with db.job(name) as (conn, stats):
            {"eop": build.step_eop, "fx": build.step_fx, "normalize": build.step_normalize,
             "derive": build.step_derive, "publish": build.step_publish}[name](conn, stats)
            out[name] = stats


def drain(step, lane, waiting, work, budget):
    """Until nothing the chain waits for is left in a lane's queue, or the budget is spent. If the lane is
    free, read here (only what the chain waits for); if a long run holds it, that run takes these first,
    so wait for it. Returns {read_here, left}."""
    deadline, here = time.monotonic() + budget, False
    with db.connect(autocommit=True) as conn:
        while waiting(conn) and time.monotonic() < deadline:
            try:
                with db.job(step, lane=lane) as (c, st):
                    work(c, min(300, deadline - time.monotonic()), st)
                    here = True
            except db.Busy:
                time.sleep(30)
        return {"read_here": here, "left": waiting(conn)}


def freshness(conn):
    """[problems]: each source where the D-1 promise says it should be. Empty = all good."""
    bad = []
    last = conn.execute("SELECT max(day) FROM ops.eop_day WHERE published").fetchone()[0]
    if not last or str(last) < yesterday():
        bad.append(f"ЕОП: последният ден при нас е {last}, очаква се {yesterday()}")
    pub = conn.execute("SELECT at FROM ops.published WHERE id = 1").fetchone()
    if not pub or pub[0] < dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=26):
        bad.append(f"сайтът не е обновяван от {pub[0] if pub else 'никога'}")
    n = conn.execute("""SELECT count(*) FROM eopsvc.queue WHERE status = 'pending' AND reason = ANY(%s)
                        AND next_at < now() - interval '1 day'""", (list(eop_offers.FIRST),)).fetchone()[0]
    if n:
        bad.append(f"оферти: {n} поръчки чакат над ден")
    n = conn.execute(f"""SELECT count(*) FROM tr.queue WHERE status = 'pending' AND {tr_worker.FIRST}
                         AND next_at < now() - interval '1 day'""").fetchone()[0]
    if n:
        bad.append(f"ТР: {n} партиди чакат над ден")
    n = conn.execute("SELECT count(*) FROM eopsvc.queue WHERE status = 'error'").fetchone()[0]
    if n:
        bad.append(f"оферти: {n} поръчки не се четат (грешка след 6 опита)")
    for source, cause, n in conn.execute("""SELECT source, cause, count(*) FROM ops.change_log
            WHERE detected_at > now() - interval '1 day' AND (source = 'eop-file' AND cause IN ('rewritten', 'gone', 'invalid'))
            GROUP BY 1, 2""").fetchall():
        bad.append(f"{source}: {n} × {cause} (архивът на източника е променен, виж ops.change_log)")
    return bad


def changes(conn):
    """What the sources changed in the last day, by source and cause (for the card)."""
    return {f"{s}/{c}": n for s, c, n in conn.execute("""SELECT source, cause, count(*) FROM ops.change_log
        WHERE detected_at > now() - interval '1 day' GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()}


def run(final=False):
    """One call of the chain (see the module docstring). Returns the stats. The day counts as done once
    the chain has run through, problems or not (they are raised after it, as the alert); a chain that
    failed twice waits for the last call of the morning (final), so a broken step does not rebuild
    every 15 minutes."""
    day = yesterday()
    with db.connect(autocommit=True) as conn:
        if done(conn, day):
            return {"day": day, "skipped": "done"}
        failed = conn.execute("""SELECT count(*) FROM ops.job_run WHERE step = 'daily' AND status = 'failed'
                                 AND inputs->>'day' = %s""", (day,)).fetchone()[0]
    if failed >= 2 and not final:
        return {"day": day, "skipped": f"failed {failed} times, the {FINAL_UTC} UTC run tries again"}
    if eop.list_day(day) is None:
        if final:
            raise RuntimeError(f"ЦАИС ЕОП has not published {day}")
        return {"day": day, "waiting": True}
    out = {"day": day}
    with db.job("daily", lane="daily", inputs={"day": day}) as (conn, stats):
        build_all(out)
        with db.job("tr-seed", lane="seed") as (c, st):
            out["tr_queued"] = tr_worker.seed_from_contracts(c)
        with db.job("eop-enqueue", lane="offers-queue") as (c, st):
            out["offers_queued"] = eop_offers.enqueue(c)
        with db.job("eop-check", lane="offers-check") as (c, st):
            out["offers_check"] = eop_offers.check(c)["ok"]
        if out["offers_check"]:
            out["offers"] = drain("eop-offers", "offers", eop_offers.waiting,
                                  lambda c, b, st: eop_offers.work(c, b, st, first=True), OFFERS_BUDGET)
        out["tr"] = drain("tr-read", "tr", tr_worker.waiting,
                          lambda c, b, st: tr_worker.process(c, b, st, first=True), TR_BUDGET)
        build_all(out, ("normalize", "derive", "publish"))
        out["changes"] = changes(conn)
        out["problems"] = freshness(conn) + ([] if out["offers_check"] else
                                             ["проверката на четеца на оферти не мина: офертите не са четени"])
        stats.update(out)
    if out["problems"]:
        raise Problems(out)
    return out


class Problems(RuntimeError):
    """The chain ran, but a source is not where D-1 says it should be; carries the stats."""
    def __init__(self, out):
        super().__init__("; ".join(out["problems"]))
        self.out = out
