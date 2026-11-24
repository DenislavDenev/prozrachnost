"""db/ref/institution_municipality.csv, the manual list of which institution of the portal is which municipality, with the
evidence of each line. It is a source of its own: stored once by sha256, loaded as a batch (zakoni_src.institution_municipality),
each change logged field by field. Only its current load stays in the landing zone."""
import csv
import io

from . import pipeline
from .config import RAW, ROOT

SOURCE = "ref"
REF = "db/ref/institution_municipality.csv"
COLS = ("institution_id", "municipality_eik", "method", "evidence", "checked", "checked_by")


def rows_of(raw):
    out = {}
    for x in csv.DictReader(io.StringIO(raw.decode("utf-8"))):
        row = dict(institution_id=int(x["institution_id"]), municipality_eik=x["municipality_id"], method=x["method"],
                   evidence=x["evidence"], checked=x["checked"], checked_by=x["checked_by"])
        if str(row["institution_id"]) in out:
            raise pipeline.ShapeError(f"{REF}: институция {row['institution_id']} е два пъти")
        out[str(row["institution_id"])] = row
    return out


def current(conn, load_id):
    sql = ("SELECT institution_id, municipality_eik, method, evidence, to_char(checked, 'YYYY-MM-DD'), checked_by "
           "FROM zakoni_src.institution_municipality WHERE load_id = %s")
    return {str(x[0]): dict(zip(COLS, x)) for x in conn.execute(sql, (load_id,))}


def write(conn, load_id, rows):
    with conn.cursor().copy(f"COPY zakoni_src.institution_municipality (load_id, {', '.join(COLS)}) FROM STDIN") as cp:
        for x in rows.values():
            cp.write_row([load_id, *(x[c] for c in COLS)])


def load(conn, r, now=None):
    raw = (ROOT / REF).read_bytes()
    file = pipeline.save_raw(conn, r, SOURCE, REF, raw, RAW, name="institution_municipality.csv", media_type="text/csv")
    done = pipeline.load(conn, r, SOURCE, REF, rows_of(raw), write, raw_file_id=file.raw_file_id, current=current, now=now)
    if done.previous:     # landing policy `latest`
        conn.execute("DELETE FROM zakoni_src.institution_municipality WHERE load_id = %s", (done.previous,))
    return done
