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
