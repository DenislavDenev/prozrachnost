"""How tests/fixtures/dfz/fy20NN.csv were cut from the real files of the archive (dfz/<year>/<day>.<sha12>.csv).

    python tools/cut_fixtures.py <fy2024.csv> <fy2025.csv> tests/fixtures/dfz

A fixture is a handful of whole blocks (the ОБЩО row and the payment rows below it) in the order of the real file, the
cells byte-for-byte, in cp1251 with a line feed, quoted like the original. Nothing is written by hand except the names of
people: a natural person's name and last name become "физическо лице №N" and "ФЛ", the firm name of a sole trader (ЕТ)
becomes "ЕТ Образец №N", so that no real person is in the repository. The same original name gets the same N, so two
different people with one name in one municipality stay two blocks with one name. Company names are public and stay.
"""
import csv
import io
import re
import sys
from pathlib import Path

NAME, SURNAME, OBLAST, OBSHTINA, MEASURE = 0, 1, 3, 4, 6

# (name as in the file, municipality or None): legal entities, public names
LEGAL = [
    ("БГ Агро Земеделска Компания ЕООД", "Вълчи дол"), ("БГ Агро Плодова Компания ЕООД", "Варна"),
    ("АГРОСЕРВИЗ КОМТРАК - ТРЪСТЕНИК EООД", "Долна Митрополия"), ("КАНЬОН ПАРК EООД", "Белоградчик"), ("Магура АД", "Белоградчик"),
    ("Община Белоградчик", "Белоградчик"), ("Прогрес2005 ООД", "Брегово"), ("ЗКПУНива", "Брегово"), ("ГЕЙВ ООД", "Видин"),
    ("Стил Трейд ООД", "Роман"), ("Марти 14ЕООД", "Враца"), ("Кооперация ГРАДИНА - 2018", "Горна Оряховица"),
    ("ГПК НАРКООП БЯЛА", "Бяла"), ("ПТК Урожай  с.Дончево", "Добрич-селска"), ("ЗК АГРА", "Добрич-селска"),
    ("Олопласт Комерс ЕООД", "Пирдоп"), ("Община Пирдоп", "Пирдоп"), ("Осиковица EООД", "Правец"),
    ("Професионална гимназия по земеделие", "Кнежа"), ("ЧПТК ПРОГРЕС", "Левски"), ("ЧПТКНОВ ЖИВОТ", "Никопол"),
]
# sole traders and people, chosen for what they show: renamed in the fixture
SOLE = [("ЕТ Светла Христова2005", "Кнежа"), ("ET   Камен Шишков", "Бяла"), ("ЕТВАСИЛЕНАЗДРАВКО БУЗОЛОВ", "Долни Дъбник")]
PEOPLE = [("Георги", "Тончев", "Велико Търново"),      # a recipient whose payments do not add up to its ОБЩО row (duplicated rows)
          ("Ангел", "Борисов", "Първомай"), ("Крум", "Иванов", "Червен бряг"), ("Марина", "Димитрова", "Стара Загора"),
          ("Жан", "Балабанов", "Кочериново"), ("Цанко", "Станков", "Столична"), ("Момчил", "Върбанов", "Враца")]
SAME_NAME = ("Димитър", "Димитров", "Сливен")           # several people with one name in one municipality: the two shortest blocks
BYALA_VARNA = "Варна"                                    # a recipient in the other Бяла


def read(path):
    text = Path(path).read_bytes().decode("cp1251")
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=";"))
    head, rows = rows[0], rows[1:]
    blocks = []
    for r in rows:
        if r[MEASURE] == "ОБЩО":
            blocks.append([r])
        else:
            blocks[-1].append(r)
    return head, blocks


def pick(blocks):
    chosen = {}
    def take(i):
        chosen[i] = blocks[i]
    for i, b in enumerate(blocks):
        o = b[0]
        for name, muni in LEGAL:
            if o[NAME] == name and o[SURNAME] == "-" and o[OBSHTINA] == muni and not (muni == "Бяла" and o[OBLAST] != "Русе"):
                take(i)
        for name, muni in SOLE:
            if o[NAME] == name and o[OBSHTINA] == muni:
                take(i)
        for name, sur, muni in PEOPLE:
            if (o[NAME], o[SURNAME], o[OBSHTINA]) == (name, sur, muni):
                take(i)
        if o[OBLAST] == BYALA_VARNA and o[OBSHTINA] == "Бяла" and o[SURNAME] == "-" and len(b) <= 12 and not re.match(r"\s*(ЕТ|ET)", o[NAME]):
            take(i)
    same = sorted((i for i, b in enumerate(blocks) if (b[0][NAME], b[0][SURNAME], b[0][OBSHTINA]) == SAME_NAME), key=lambda i: len(blocks[i]))[:2]
    for i in same:
        take(i)
    for oblast in ("Видин", "Бургас", "Хасково", "Смолян"):
        k = 0
        for i, b in enumerate(blocks):
            if b[0][OBLAST] == oblast and b[0][SURNAME] != "-" and 3 <= len(b) <= 6 and k < 2:
                take(i)
                k += 1
    return [chosen[i] for i in sorted(chosen)]


def anonymise(blocks):
    ids = {}
    def n(key):
        return ids.setdefault(key, len(ids) + 1)
    out = []
    for b in blocks:
        o = b[0]
        if o[SURNAME] != "-":
            k = n(("p", o[NAME], o[SURNAME], o[OBLAST], o[OBSHTINA]))
            name, sur = f"физическо лице №{k}", "ФЛ"
        elif re.match(r"\s*(ЕТ|ET)", o[NAME]):
            k = n(("e", o[NAME], o[OBLAST], o[OBSHTINA]))
            name, sur = f"ЕТ Образец №{k}", "-"
        else:
            out.append(b)
            continue
        out.append([[name, sur, *r[2:]] for r in b])
    return out


def write(path, head, blocks):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(head)
    for b in blocks:
        w.writerows(b)
    Path(path).write_bytes(buf.getvalue().encode("cp1251"))


if __name__ == "__main__":
    src24, src25, out = sys.argv[1:4]
    for fy, src in ((2024, src24), (2025, src25)):
        head, blocks = read(src)
        chosen = anonymise(pick(blocks))
        write(Path(out) / f"fy{fy}.csv", head, chosen)
        print(fy, len(chosen), "blocks", sum(len(b) for b in chosen), "lines")
