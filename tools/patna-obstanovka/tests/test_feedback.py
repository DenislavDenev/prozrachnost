"""feedback.py is copied unchanged into every tool, and so is this test."""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))
import feedback as F  # noqa: E402


def test_issue_is_a_titled_quote_with_page_and_time_and_no_mentions():
    i = F.issue("problem", "Сумата е грешна @someone\n\nвтори ред", "https://tender.denev.work/tenders/1?a=b",
                "Поръчка [1] | Тендер", datetime(2026, 9, 27, 21, 14))
    assert i["title"] == "Проблем: Сумата е грешна @​someone втори ред"
    assert i["labels"] == ["bug", "обратна връзка"]
    b = i["body"]
    assert b.startswith("### Проблем\n\n> Сумата е грешна @​someone\n>\n> втори ред\n\n---\n")
    assert "@someone" not in b
    assert "**Страница:** [Поръчка 1 Тендер](https://tender.denev.work/tenders/1?a=b)" in b
    assert "**Изпратено:** 27.09.2026, 21:14 (София)" in b and "Браузър" not in b
    assert F.issue("idea", "x" * 100)["title"] == "Предложение: " + "x" * 80 + "…"
    assert "**Номер:** `ABCD-2345`  \n**Страница:**" in F.issue("idea", "текст", "https://x.bg/", code="ABCD-2345")["body"]


def test_the_sender_gets_a_random_number_that_is_also_in_the_issue(tmp_path):
    import re
    sent = []
    F._hits.clear()
    msgs = [F.accept({"kind": "idea", "text": f"идея {n}"}, str(n), "", tmp_path / "s", "o/r",
                     lambda r, p: sent.append(p) or True)[1] for n in range(2)]
    codes = [re.search(r"Номерът му е ([2-9A-Z]{4}-[2-9A-Z]{4})\.", m).group(1) for m in msgs]
    assert codes[0] != codes[1] and all(f"`{c}`" in p["body"] for c, p in zip(codes, sent))
    assert not set("01ILOU") & set("".join(F.new_code() for _ in range(200)))
    F._hits.clear()


def test_a_page_that_is_not_a_plain_url_is_not_linked():
    b = F.issue("idea", "текст", "javascript:alert(1)")["body"]
    assert "javascript" not in b and "не е посочена" in b
    assert "](https://x.bg/a)" not in F.issue("idea", "текст", "https://x.bg/a) [b](https://evil")["body"]


def test_nothing_is_lost_when_github_does_not_take_it(tmp_path):
    spool, sent = tmp_path / "s.jsonl", []
    F._hits.clear()
    st, _ = F.accept({"kind": "problem", "text": "първо"}, "a", "", spool, "o/r", send=lambda r, p: False)
    assert st == 200 and len(spool.read_text(encoding="utf-8").splitlines()) == 1
    F.accept({"kind": "idea", "text": "второ"}, "b", "", spool, "o/r", send=lambda r, p: sent.append(p) or True)
    assert [p["title"] for p in sent] == ["Проблем: първо", "Предложение: второ"]
    assert spool.read_text(encoding="utf-8") == ""


def test_bots_bad_input_and_floods_do_not_become_issues(tmp_path):
    spool, sent = tmp_path / "s.jsonl", []
    send = lambda r, p: sent.append(p) or True  # noqa: E731
    F._hits.clear()
    assert F.accept({"kind": "idea", "text": "спам", "website": "x"}, "a", "", spool, "o/r", send)[0] == 200
    assert F.accept({"kind": "other", "text": "нещо"}, "a", "", spool, "o/r", send)[0] == 400
    assert F.accept({"kind": "idea", "text": "ab"}, "a", "", spool, "o/r", send)[0] == 400
    assert sent == []
    codes = [F.accept({"kind": "idea", "text": f"идея {n}"}, "a", "", spool, "o/r", send)[0] for n in range(6)]
    assert codes == [200] * 5 + [429]
    F._hits.clear()
    codes = [F.accept({"kind": "idea", "text": f"идея {n}"}, str(n), "", spool, "o/r", send)[0] for n in range(31)]
    assert codes == [200] * 30 + [429]
    F._hits.clear()


def test_the_referer_is_the_page_when_the_form_has_none(tmp_path):
    sent = []
    F._hits.clear()
    F.accept({"kind": "idea", "text": "без JS"}, "a", "https://x.bg/p", tmp_path / "s", "o/r",
             lambda r, p: sent.append(p) or True)
    assert "(https://x.bg/p)" in sent[0]["body"]
    F._hits.clear()


def test_button_and_support_link():
    assert 'popovertarget="fb-pop"' in F.BUTTON and 'action="/feedback"' in F.BUTTON and 'name="website"' in F.BUTTON
    assert F.support_link("http://hub/") == '<a class="fb-support" href="http://hub/podkrepi">Подкрепи проекта</a>'


def test_the_access_log_keeps_no_line_of_the_form():
    import logging
    F.router("o/r", "/tmp")
    log = logging.getLogger("uvicorn.access")
    line = lambda path: logging.LogRecord("uvicorn.access", 20, "", 0, '%s - "%s %s HTTP/%s" %d',  # noqa: E731
                                          ("1.2.3.4:5", "POST", path, "1.1", 200), None)
    assert not log.filter(line("/feedback")) and log.filter(line("/tenders/1"))



def test_name_and_email_fields_only_when_mail_is_set_up():
    assert 'name="email"' in F.button(True) and 'name="email"' not in F.button(False)


class Mailbox(list):
    def __call__(self, to, subject, text):
        self.append((to, subject, text))
        return not getattr(self, "down", False)


def test_an_address_stays_here_and_gets_one_letter_to_confirm(tmp_path, monkeypatch):
    monkeypatch.setenv("MAIL_SMTP_HOST", "smtp.test")
    monkeypatch.setenv("MAIL_FROM", "x@test")
    box, sent = Mailbox(), []
    F._hits.clear()
    st, msg = F.accept({"kind": "problem", "text": "грешна сума", "name": "Ива", "email": "iva@example.bg"}, "a", "",
                       tmp_path / "s.jsonl", "o/r", lambda r, p: sent.append(p) or 17, "https://t.bg/", box)
    assert st == 200 and "потвърди адреса" in msg
    assert "iva@example.bg" not in json.dumps(sent) and "Ива" not in json.dumps(sent)       # never in the issue
    (to, subject, text), = box
    assert to == "iva@example.bg" and subject.startswith("Обратна връзка ") and "Здравей, Ива!" in text
    with F.closing(F.db(tmp_path)) as con:
        row = con.execute("SELECT * FROM follower").fetchone()
    assert row["issue"] == 17 and row["invited"] and not row["confirmed"]
    assert f"https://t.bg/feedback/confirm/{row['token']}" in text
    assert F.accept({"kind": "idea", "text": "още", "email": "не-е-имейл"}, "b", "", tmp_path / "s.jsonl", "o/r",
                    lambda r, p: 1, "https://t.bg/", box)[0] == 400
    for n in range(4):                                  # someone signing up another person's address
        F.accept({"kind": "idea", "text": f"спам {n}", "email": "iva@example.bg"}, f"c{n}", "", tmp_path / "s.jsonl",
                 "o/r", lambda r, p: 1, "https://t.bg/", box)
    assert len(box) == 3
    F._hits.clear()


def test_without_mail_an_address_is_not_even_kept(tmp_path, monkeypatch):
    monkeypatch.delenv("MAIL_SMTP_HOST", raising=False)
    F._hits.clear()
    st, _ = F.accept({"kind": "idea", "text": "една идея", "email": "iva@example.bg"}, "a", "", tmp_path / "s.jsonl", "o/r",
             lambda r, p: 1, "https://t.bg/", Mailbox())
    assert st == 200 and not (tmp_path / "feedback.db").exists()
    F._hits.clear()


def follower(tmp_path, confirmed=True, created=1000.0):
    with F.closing(F.db(tmp_path)) as con, con:
        con.execute("INSERT INTO follower (code, site, name, email, token, issue, created, invited, confirmed) "
                    "VALUES ('ABCD-2345', 'https://t.bg/', NULL, 'a@b.bg', 'tok', 5, ?, ?, ?)",
                    (created, created, created if confirmed else None))


def test_watch_tells_answers_and_status_and_keeps_the_rest_inside(tmp_path):
    follower(tmp_path)
    gh = {"/repos/o/r/issues/5": {"state": "closed", "state_reason": "completed"},
          "/repos/o/r/issues/5/comments?per_page=100": [
              {"id": 1, "author_association": "OWNER", "body": "вътрешна бележка"},
              {"id": 2, "author_association": "OWNER", "body": "Отговор: поправихме сумата."},
              {"id": 3, "author_association": "NONE", "body": "Отговор: чужд човек"}]}
    box = Mailbox()
    F.watch(tmp_path, "o/r", gh.get, box, lambda r, p: 1, now=2000)
    assert [s.split(": ", 1)[1] for _, s, _ in box] == ["отговор", "затворено: поправено"]
    assert "поправихме сумата." in box[0][2] and "вътрешна" not in box[0][2] and "https://t.bg/feedback/stop/tok" in box[0][2]
    F.watch(tmp_path, "o/r", gh.get, box, lambda r, p: 1, now=3000)
    assert len(box) == 2                                                  # nothing new, nothing sent
    gh["/repos/o/r/issues/5"] = {"state": "open", "state_reason": "reopened"}
    F.watch(tmp_path, "o/r", gh.get, box, lambda r, p: 1, now=4000)
    assert box[-1][1].endswith("отворено отново")


def test_a_letter_that_did_not_go_out_is_tried_again(tmp_path):
    follower(tmp_path)
    gh = {"/repos/o/r/issues/5": {"state": "closed", "state_reason": "not_planned"},
          "/repos/o/r/issues/5/comments?per_page=100": [{"id": 9, "author_association": "OWNER", "body": "Отговор: не."}]}
    box = Mailbox()
    box.down = True
    F.watch(tmp_path, "o/r", gh.get, box, lambda r, p: 1, now=2000)
    box.down = False
    box.clear()
    F.watch(tmp_path, "o/r", gh.get, box, lambda r, p: 1, now=3000)
    assert [s.split(": ", 1)[1] for _, s, _ in box] == ["отговор", "затворено"]


def test_addresses_are_forgotten_on_time(tmp_path):
    follower(tmp_path, confirmed=False, created=0)
    F.watch(tmp_path, "o/r", {}.get, Mailbox(), lambda r, p: 1, now=F.CONFIRM_DAYS * 86400 + 1)
    with F.closing(F.db(tmp_path)) as con:
        assert con.execute("SELECT count(*) FROM follower").fetchone()[0] == 0
    follower(tmp_path)
    with F.closing(F.db(tmp_path)) as con, con:
        con.execute("UPDATE follower SET state = 'completed', closed = 1000")
    F.watch(tmp_path, "o/r", {"/repos/o/r/issues/5": {"state": "closed", "state_reason": "completed"},
                              "/repos/o/r/issues/5/comments?per_page=100": []}.get,
            Mailbox(), lambda r, p: 1, now=1000 + F.KEEP_DAYS * 86400 + 1)
    with F.closing(F.db(tmp_path)) as con:
        assert con.execute("SELECT count(*) FROM follower").fetchone()[0] == 0
