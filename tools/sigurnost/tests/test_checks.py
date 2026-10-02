"""The reconciliations, on the real tables of 2024. A check that is never seen to fail guards nothing: each has a case that differs."""
import copy
from decimal import Decimal

from ingest import checks, gold, sheet
from tests import helpers as h

resolve = gold.resolver()


def blocks(res):
    return sheet.parse(h.fixture(h.POLICE, res)).blocks


def by_id(results, check_id):
    return [r for r in results if r.check_id == check_id]


def test_the_structures_add_up_to_the_total_row_exactly():
    """Resource 0e35ccef: 28 oblasts and 3 general directorates; registered 74709, with an unknown offender 60513, solved 36944,
    solved with an unknown offender 25328, difference 0 in each column."""
    res = checks.structures_sum(blocks(h.B)[0])
    assert {r.scope: (r.expected, r.got) for r in res} == {
        "reg": (74709, 74709), "reg_unknown": (60513, 60513), "solved": (36944, 36944), "solved_unknown": (25328, 25328)}
    assert all(r.status == checks.OK and r.gating for r in res)


def test_a_structure_changed_by_one_makes_the_sum_differ():
    b = blocks(h.B)[0]
    r = next(x for x in b.rows if x.text == "Варна")
    r.cells[0] = (2, "6536", Decimal(6536), False)
    res = checks.structures_sum(b)
    bad = [x for x in res if x.status == checks.DIFFERS]
    assert [(x.scope, x.diff) for x in bad] == [("reg", 1)]


def test_the_two_total_rows_are_one_number():
    res = checks.types_vs_structures(blocks(h.A)[0], blocks(h.B)[0])
    assert len(res) == 4 and all(r.status == checks.OK for r in res)
    a = blocks(h.A)[0]
    next(x for x in a.rows if x.total).cells[3] = (5, "36945", Decimal(36945), False)
    assert [r.scope for r in checks.types_vs_structures(a, blocks(h.B)[0]) if r.status == checks.DIFFERS] == ["solved"]


def test_each_block_total_is_its_row_by_structures_and_the_blocks_add_up_to_the_country():
    """Resource 9d2cc270 against 0e35ccef: 28 oblasts' block totals equal their rows; the oblasts (72716) and the three
    directorates (1993) make the country's block (74709)."""
    res = checks.blocks_vs_structures(blocks(h.D), blocks(h.B)[0], resolve)
    assert len(res) == 2 * (28 + 2)
    assert all(r.status == checks.OK for r in res), [r for r in res if r.status != checks.OK]
    country = next(r for r in res if r.scope == "reg:BG")
    assert (country.expected, country.got) == (74709, 74709)


def test_a_block_that_does_not_match_its_oblast_differs_and_an_unknown_structure_is_a_difference():
    bl = blocks(h.D)
    t = next(r for r in bl[2].rows if r.total)             # Варна
    t.cells[0] = (2, "6534", Decimal(6534), False)
    res = checks.blocks_vs_structures(bl, blocks(h.B)[0], resolve)
    assert "reg:BG331" in {r.scope for r in res if r.status == checks.DIFFERS}
    bl = blocks(h.D)
    bl[0].structure = "Атлантида"
    res = checks.blocks_vs_structures(bl, blocks(h.B)[0], resolve)
    assert any(r.scope == "Атлантида" and r.detail == "непозната структура" for r in res)


def test_the_printed_share_of_solved_is_the_formula_in_all_the_rows():
    """3 748 rows of five tables: solved / registered x 100 is the printed share to 0.01, and solved never exceeds registered."""
    for res, fam in ((h.A, "types"), (h.B, "structures"), (h.C, "main"), (h.D, "types_by_structure"), (h.E, "econ_by_structure")):
        bl = blocks(res)
        assert checks.clearance_formula(bl, fam).got == 0, res
        assert checks.solved_le_registered(bl, fam).got == 0, res


def test_a_wrong_printed_share_is_counted():
    b = blocks(h.B)
    next(x for x in b[0].rows if x.text == "Варна").cells[4] = (6, "39.9", Decimal("39.9"), False)
    r = checks.clearance_formula(b, "structures")
    assert r.got == 1 and r.status == checks.SOURCE and not r.gating


def test_the_points_add_up_to_their_subpoints_where_the_source_does_and_the_gap_is_named_where_it_does_not():
    """Resource ef7a8f29. Points 1 and 2 are exactly the sum of their sub-points (8510, 32321). Point 3 is 86 above its six
    sub-points, and the row printed as "4. Други общоопасни" holds exactly 86: it is the seventh part of point 3 whose number is
    shifted, so the numbers after it (4.1.-4.7.) hang under a point of 86 crimes and add up to 13 853. The total row (74709) is
    not the sum of the numbered points (65362) and no subset of the printed rows makes it (searched on 02.10.2026): the
    difference is in the original and is reported, not hidden."""
    res = checks.point_sums(blocks(h.A)[0], "types")
    d = {r.scope: r for r in res}
    assert (d["1."].expected, d["1."].got, d["1."].status) == (8510, 8510, checks.OK)
    assert (d["2."].expected, d["2."].got, d["2."].status) == (32321, 32321, checks.OK)
    assert (d["3."].expected, d["3."].got, d["3."].status) == (24445, 24359, checks.SOURCE)
    assert d["3."].diff == -d["4."].expected == -86
    assert (d["4."].expected, d["4."].got, d["4."].status) == (86, 13853, checks.SOURCE)
    assert (d["total:points"].expected, d["total:points"].got, d["total:points"].diff) == (74709, 65362, -9347)
    assert all(not r.gating for r in res)


def test_a_part_inside_a_sub_point_is_never_added_to_the_points():
    """"1.1. Убийство" (237) has "·" parts below it; adding them to the sum of point 1 would give more than 8510."""
    b = blocks(h.A)[0]
    parts = [r for r in b.rows if r.level >= 3]
    assert parts and sum(r.cells[0][2] for r in parts) > 0
    d = {r.scope: r for r in checks.point_sums(b, "types")}
    assert d["1."].got == d["1."].expected
