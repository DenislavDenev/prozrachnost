"""Обратна връзка: the rightmost header button on every page files an issue in the tool's own GitHub repo.

The same file is copied unchanged into every tool of this repo (AGENTS.md, "Обратна връзка"; CI checks the copies).
An app does three things:
    app.include_router(feedback.router("DenislavDenev/prozrachnost", data_dir, "<Име на инструмента>"))
    feedback.BUTTON                  -> last element of the header
    feedback.support_link(hub_url)   -> last element of the footer ("Подкрепи проекта", the hub's /podkrepi)
and a timer runs `python feedback.py watch <data_dir> <owner/repo>` every 15 minutes.

The token is GITHUB_ISSUES_TOKEN (fine-grained, Issues read/write only), from /etc/prozrachnost/feedback.env.
Nothing is lost: a message goes to the spool first and leaves it only when GitHub has created the issue.
The sender gets a random number (7K3F-9Q2M), also in the issue; GitHub's #N stays inside.
An issue carries the page and the time, nothing about the sender. The IP only counts the hourly limit in memory,
and the access log leaves out /feedback, so where a message came from is written nowhere.

Updates by e-mail (only when MAIL_SMTP_HOST and MAIL_FROM are set; until then the form has no name/e-mail fields):
a sender may leave a name and an address. They stay on this server (<data_dir>/feedback.db), never in the issue.
The address gets one letter to confirm; after that `watch` writes when the issue is closed or reopened and when
the team comments with a line starting „Отговор:“. Other comments stay inside. An address is deleted 30 days after
the issue closes, 7 days after an unconfirmed request, or at once from the link in every letter.
"""
import json
import logging
import os
import re
import secrets
import smtplib
import sqlite3
import threading
import time
import urllib.request
from contextlib import closing
from datetime import datetime
from email.message import EmailMessage
from html import escape
from urllib.parse import parse_qs
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

try:
    import fcntl
except ImportError:  # ponytail: Windows dev runs are one process, the thread lock is enough there
    fcntl = None

KINDS = {"problem": ("Проблем", "проблем"), "idea": ("Предложение", "предложение")}
LABEL = "обратна връзка"
MAX_TEXT = 4000
PER_CLIENT, PER_HOUR = 5, 30  # messages an hour: from one sender, from everyone (per worker)
CODE_CHARS = "23456789ABCDEFGHJKMNPQRSTVWXYZ"  # no 0/O, 1/I/L, U: easy to read out and type
URL = re.compile(r"https?://[^\s<>()\[\]|`\"']{1,500}")
EMAIL = re.compile(r"[^@\s<>\"',;]{1,64}@[^@\s<>\"',;]+\.[^@\s<>\"',;]{2,}")
ANSWER = "Отговор:"                         # a team comment starting with this goes to the sender
TEAM = {"OWNER", "MEMBER", "COLLABORATOR"}
KEEP_DAYS, CONFIRM_DAYS, MAX_PENDING = 30, 7, 3
_lock = threading.Lock()
_hits: dict[str, list[float]] = {}


def mail_on():
    return bool(os.getenv("MAIL_SMTP_HOST") and os.getenv("MAIL_FROM"))


def new_code():
    """The number a sender gets instead of the issue's #N: random, so it says nothing about how many came before."""
    c = "".join(secrets.choice(CODE_CHARS) for _ in range(8))
    return f"{c[:4]}-{c[4:]}"


def issue(kind, text, page="", page_title="", now=None, code="", tool=""):
    """The GitHub issue for one message: a titled quote of the text, then where and when it was sent."""
    name, label = KINDS[kind]
    text = text.strip().replace("@", "@​")  # no @mentions: a sender must not ping anyone on GitHub
    line = " ".join(text.split())
    title = f"[{name}] {tool + ': ' if tool else ''}{line[:80]}{'…' if len(line) > 80 else ''}"
    quote = "\n".join(f"> {l}" if l.strip() else ">" for l in text.splitlines())
    page = page if URL.fullmatch(page or "") else ""
    shown = " ".join(re.sub(r"[\[\]`|\\]", "", page_title or "").split())[:150] or page
    when = (now or datetime.now(ZoneInfo("Europe/Sofia"))).strftime("%d.%m.%Y, %H:%M")
    meta = [f"**Номер:** `{code}`"] if code else []
    meta += [f"**Страница:** [{shown}]({page})" if page else "**Страница:** не е посочена",
             f"**Изпратено:** {when} (София)"]
    body = (f"### {name}\n\n{quote}\n\n---\n\n" + "  \n".join(meta)
            + "\n\n<sub>Изпратено с бутона „Обратна връзка“ на сайта. Отговор към подателя: коментар, който започва с"
            + f" „{ANSWER}“.</sub>\n")
    return {"title": title, "body": body, "labels": [label, LABEL] + ([tool.lower()] if tool else [])}


def github(path, payload=None):
    """One GitHub API call with the issues token; the parsed answer, or None."""
    token = os.getenv("GITHUB_ISSUES_TOKEN")
    if not token:
        return None
    req = urllib.request.Request(f"https://api.github.com{path}", method="POST" if payload else "GET",
                                 data=json.dumps(payload).encode() if payload else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Prozrachnost-feedback"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except (OSError, ValueError) as e:
        print(f"feedback: GitHub {path}: {e}", flush=True)
        return None


def post(repo, payload):
    """Create the issue; its number, or None when GitHub did not take it."""
    return (github(f"/repos/{repo}/issues", payload) or {}).get("number")


def flush(spool: Path, repo, item=None, send=None):
    """Append item (if any) to the spool, send everything in it, keep what GitHub did not take.
    Returns how many are still waiting."""
    send = post if send is None else send
    spool.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(spool, "a+", encoding="utf-8") as f:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)  # two uvicorn workers share one spool
        if item:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            f.flush()
        f.seek(0)
        left = []
        for line in filter(str.strip, f.read().splitlines()):
            it = json.loads(line)
            number = send(repo, it.get("issue", it))
            if not number:
                left.append(line)
            elif type(number) is int and it.get("code") and (spool.parent / "feedback.db").exists():
                with closing(db(spool.parent)) as con, con:
                    con.execute("UPDATE follower SET issue = ? WHERE code = ?", (number, it["code"]))
        f.seek(0)
        f.truncate()
        f.write("".join(l + "\n" for l in left))
    return len(left)


def allowed(client, now=None):
    now = now or time.time()
    with _lock:
        for k in list(_hits):
            _hits[k] = [t for t in _hits[k] if now - t < 3600]
            if not _hits[k]:
                del _hits[k]
        if len(_hits.get(client, ())) >= PER_CLIENT or sum(map(len, _hits.values())) >= PER_HOUR:
            return False
        _hits.setdefault(client, []).append(now)
        return True


def db(data_dir):
    con = sqlite3.connect(Path(data_dir) / "feedback.db", timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS follower (code TEXT PRIMARY KEY, site TEXT NOT NULL, name TEXT,
        email TEXT NOT NULL, token TEXT UNIQUE NOT NULL, issue INTEGER, created REAL NOT NULL, invited REAL,
        confirmed REAL, state TEXT NOT NULL DEFAULT 'open', last_comment INTEGER NOT NULL DEFAULT 0, closed REAL)""")
    return con


def send_mail(to, subject, text):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = os.environ["MAIL_FROM"], to, subject
    msg.set_content(text)
    port = int(os.getenv("MAIL_SMTP_PORT", "587"))
    try:
        with (smtplib.SMTP_SSL if port == 465 else smtplib.SMTP)(os.environ["MAIL_SMTP_HOST"], port, timeout=20) as s:
            if port != 465:
                s.starttls()
            if os.getenv("MAIL_SMTP_USER"):
                s.login(os.environ["MAIL_SMTP_USER"], os.environ["MAIL_SMTP_PASSWORD"])
            s.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as e:
        print(f"feedback: a letter did not go out: {e}", flush=True)  # the address is never logged
        return False


def letter(what, row, answer=""):
    """(subject, text) of one letter to a follower."""
    code, site, token = row["code"], row["site"].rstrip("/"), row["token"]
    subject, body = {
        "confirm": ("потвърди известията",
                    f"Получихме съобщението ти с номер {code}. За да ти пишем, когато отговорим или го затворим,"
                    f" потвърди адреса си тук:\n{site}/feedback/confirm/{token}\n\nАко не си го пратил ти, не прави"
                    f" нищо: адресът се изтрива след {CONFIRM_DAYS} дни."),
        "answer": ("отговор", f"Отговорихме на съобщението ти с номер {code}:\n\n{answer}"),
        "completed": ("затворено: поправено", f"Съобщението ти с номер {code} е затворено: поправихме го. Благодарим!"),
        "not_planned": ("затворено", f"Съобщението ти с номер {code} е затворено: решихме да не го правим."),
        "closed": ("затворено", f"Съобщението ти с номер {code} е затворено."),
        "reopened": ("отворено отново", f"Съобщението ти с номер {code} е отворено отново."),
    }[what]
    hi = f"Здравей, {row['name']}!" if row["name"] else "Здравей!"
    return (f"Обратна връзка {code}: {subject}",
            f"{hi}\n\n{body}\n\n--\nПрозрачност\nДа не получаваш повече писма и да изтрием адреса ти: {site}/feedback/stop/{token}\n")


def follow(data_dir, code, site, name, email, mail=None, now=None):
    """Keep who wants updates for this message and ask them to confirm. False when the address has too many
    unconfirmed requests (someone may be signing up another person)."""
    mail, now = send_mail if mail is None else mail, now or time.time()
    with closing(db(data_dir)) as con, con:
        if con.execute("SELECT count(*) FROM follower WHERE email = ? AND confirmed IS NULL", (email,)).fetchone()[0] >= MAX_PENDING:
            return False
        con.execute("INSERT INTO follower (code, site, name, email, token, created) VALUES (?, ?, ?, ?, ?, ?)",
                    (code, site, name or None, email, secrets.token_urlsafe(24), now))
        row = con.execute("SELECT * FROM follower WHERE code = ?", (code,)).fetchone()
    if mail(email, *letter("confirm", row)):  # outside the transaction: SMTP may take seconds
        with closing(db(data_dir)) as con, con:
            con.execute("UPDATE follower SET invited = ? WHERE code = ?", (now, code))
    return True


def accept(form, client, referer, spool, repo, send=None, base="", mail=None, tool=""):
    """One submitted form -> (HTTP status, message for the sender)."""
    if form.get("website"):  # the hidden field only bots fill in
        return 200, "Благодарим! Получихме го."
    kind, text = form.get("kind", ""), (form.get("text") or "").strip()
    if kind not in KINDS or not 5 <= len(text) <= MAX_TEXT:
        return 400, f"Избери вид и напиши между 5 и {MAX_TEXT} знака."
    email = (form.get("email") or "").strip() if mail_on() else ""
    if email and (len(email) > 254 or not EMAIL.fullmatch(email)):
        return 400, "Имейлът не изглежда верен. Поправи го или го остави празен."
    if not allowed(client):
        return 429, "Получихме много съобщения за последния час. Опитай пак по-късно."
    code = new_code()
    asked = email and follow(spool.parent, code, base, " ".join((form.get("name") or "").split())[:80], email, mail)
    flush(spool, repo, {"code": code, "issue": issue(kind, text, form.get("page") or referer,
                                                     form.get("page_title", ""), code=code, tool=tool)}, send)
    return 200, (f"Благодарим! Получихме го и ще го прегледаме. Номерът му е {code}."
                 + (" Пратихме ти писмо: потвърди адреса, за да ти пишем." if asked else ""))


def watch(data_dir, repo, get=None, mail=None, send=None, now=None):
    """Every 15 minutes: send what is still in the spool, then tell each confirmed follower what changed.
    A letter that does not go out is tried again next time; nothing is marked sent before it is."""
    get = github if get is None else get
    mail, now, data_dir = send_mail if mail is None else mail, now or time.time(), Path(data_dir)
    flush(data_dir / "feedback-pending.jsonl", repo, send=send)
    if not (data_dir / "feedback.db").exists():
        return
    with closing(db(data_dir)) as con, con:
        con.execute("DELETE FROM follower WHERE confirmed IS NULL AND created < ?", (now - CONFIRM_DAYS * 86400,))
        con.execute("DELETE FROM follower WHERE closed < ?", (now - KEEP_DAYS * 86400,))
        rows = con.execute("SELECT * FROM follower WHERE confirmed IS NULL AND invited IS NULL").fetchall()
    for row in rows:
        if mail(row["email"], *letter("confirm", row)):
            with closing(db(data_dir)) as con, con:
                con.execute("UPDATE follower SET invited = ? WHERE code = ?", (now, row["code"]))
    with closing(db(data_dir)) as con:
        rows = con.execute("SELECT * FROM follower WHERE confirmed IS NOT NULL AND issue IS NOT NULL").fetchall()
    for row in rows:
        it = get(f"/repos/{repo}/issues/{row['issue']}")
        comments = get(f"/repos/{repo}/issues/{row['issue']}/comments?per_page=100")  # ponytail: first 100 comments
        if it is None or comments is None:
            continue
        last = row["last_comment"]
        for c in comments:
            said = (c.get("body") or "").lstrip()
            if c["id"] <= last:
                continue
            if c.get("author_association") in TEAM and said.startswith(ANSWER):
                if not mail(row["email"], *letter("answer", row, said[len(ANSWER):].strip())):
                    break
            last = c["id"]
        state = "open" if it["state"] == "open" else it.get("state_reason") or "closed"
        if state not in ("open", "completed", "not_planned"):
            state = "closed"
        told = state == row["state"] or mail(row["email"], *letter("reopened" if state == "open" else state, row))
        with closing(db(data_dir)) as con, con:
            con.execute("UPDATE follower SET last_comment = ?, state = ?, closed = ? WHERE code = ?",
                        (last, state if told else row["state"],
                         (row["closed"] or now) if told and state != "open" else (None if told else row["closed"]),
                         row["code"]))


class _NoFeedbackLog(logging.Filter):
    """uvicorn's access line is `client - "METHOD path HTTP/x"`: drop it for /feedback, keep every other request."""
    def filter(self, r):
        return not (isinstance(r.args, tuple) and len(r.args) > 2 and str(r.args[2]).startswith("/feedback"))


def _page(msg, form=""):
    return HTMLResponse(f'<!doctype html><html lang="bg"><meta charset="utf-8"><meta name="viewport" '
                        f'content="width=device-width,initial-scale=1"><title>Обратна връзка</title>'
                        f'<body style="font:17px/1.5 sans-serif;max-width:560px;margin:60px auto;padding:0 16px">'
                        f'<h1 style="font-size:24px">Обратна връзка</h1><p>{escape(msg)}</p>{form}'
                        f'<p><a href="/">Към сайта</a></p></body></html>')


def router(repo, data_dir, tool=""):
    logging.getLogger("uvicorn.access").addFilter(_NoFeedbackLog())
    data_dir = Path(data_dir)
    spool = data_dir / "feedback-pending.jsonl"
    r = APIRouter()

    @r.post("/feedback", include_in_schema=False)
    async def feedback(request: Request):
        if int(request.headers.get("content-length") or 0) > 20000:
            return JSONResponse({"ok": False, "message": "Текстът е твърде дълъг."}, status_code=413)
        # parsed by hand: request.form() needs python-multipart, which not every tool installs
        form = {k: v[0] for k, v in parse_qs((await request.body())[:20000].decode("utf-8", "replace")).items()}
        fwd = request.headers.get("x-forwarded-for", "")
        client = fwd.split(",")[0].strip() or (request.client.host if request.client else "")
        status, msg = await run_in_threadpool(accept, form, client, request.headers.get("referer", ""),
                                              spool, repo, None, str(request.base_url), None, tool)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"ok": status == 200, "message": msg}, status_code=status)
        back = escape(request.headers.get("referer") or "/")
        return HTMLResponse(f'<!doctype html><meta charset="utf-8"><title>Обратна връзка</title>'
                            f'<p>{escape(msg)}</p><p><a href="{back}">Обратно към страницата</a></p>', status_code=status)

    # the links in the letters: GET only shows a button, so a mail scanner that opens links changes nothing
    ACTIONS = {"confirm": ("Потвърди, че искаш писма за това съобщение.", "Потвърждавам"),
               "stop": ("Да спрем писмата и да изтрием адреса ти?", "Спри и изтрий")}

    @r.get("/feedback/{action}/{token}", include_in_schema=False)
    def ask(action: str, token: str):
        if action not in ACTIONS:
            return _page("Няма такава страница.")
        text, button = ACTIONS[action]
        return _page(text, f'<form method="post"><button type="submit" style="font:inherit;padding:10px 16px">'
                           f'{button}</button></form>')

    @r.post("/feedback/{action}/{token}", include_in_schema=False)
    def act(action: str, token: str):
        if action not in ACTIONS or not (data_dir / "feedback.db").exists():
            return _page("Връзката не е валидна или вече е изтекла.")
        with closing(db(data_dir)) as con, con:
            row = con.execute("SELECT code FROM follower WHERE token = ?", (token,)).fetchone()
            if not row:
                return _page("Връзката не е валидна или вече е изтекла.")
            if action == "stop":
                con.execute("DELETE FROM follower WHERE token = ?", (token,))
                return _page(f"Готово: няма да получаваш писма за {row['code']} и адресът ти е изтрит.")
            con.execute("UPDATE follower SET confirmed = coalesce(confirmed, ?) WHERE token = ?", (time.time(), token))
            return _page(f"Потвърдено. Ще ти пишем, когато отговорим на {row['code']} или го затворим.")

    return r


def support_link(hub_url):
    return f'<a class="fb-support" href="{escape(hub_url.rstrip("/"))}/podkrepi">Подкрепи проекта</a>'


def button(mail):
    return BUTTON_TEMPLATE.replace("<!--mail-->", MAIL_FIELDS if mail else "")


MAIL_FIELDS = """<div class="fb-mail"><label for="fb-name">Име <span class="fb-opt">по желание</span></label>
<input id="fb-name" name="name" maxlength="80" autocomplete="name">
<label for="fb-email">Имейл за отговор <span class="fb-opt">по желание</span></label>
<input id="fb-email" name="email" type="email" maxlength="254" autocomplete="email">
<p class="fb-note">Пишем ти само за това съобщение: за потвърждение, после при отговор или затваряне. Името и имейлът не влизат в съобщението; изтриваме ги 30 дни след затварянето или когато се отпишеш.</p></div>
"""

BUTTON_TEMPLATE = """<button type="button" class="fb-open" popovertarget="fb-pop" aria-label="Обратна връзка: проблем или предложение"><span class="fb-long">Обратна връзка</span><span class="fb-short">Пиши ни</span></button>
<div id="fb-pop" class="fb-pop" popover role="dialog" aria-labelledby="fb-h">
<form method="post" action="/feedback">
<h2 id="fb-h">Обратна връзка</h2>
<p>Видя грешка или имаш идея? Пиши ни. Съобщението стига направо до екипа, заедно с адреса на тази страница.</p>
<fieldset><legend>Какво е</legend>
<label><input type="radio" name="kind" value="problem" checked> Проблем</label>
<label><input type="radio" name="kind" value="idea"> Предложение</label>
</fieldset>
<label for="fb-text">Опиши го</label>
<textarea id="fb-text" name="text" rows="6" minlength="5" maxlength="4000" required></textarea>
<p class="fb-note">Не пиши лични данни: текстът може да стане публичен.</p>
<!--mail-->
<input type="hidden" name="page"><input type="hidden" name="page_title">
<div class="fb-hp" aria-hidden="true"><label>Уебсайт <input name="website" tabindex="-1" autocomplete="off"></label></div>
<div class="fb-act"><button type="submit">Изпрати</button><button type="button" popovertarget="fb-pop" popovertargetaction="hide">Затвори</button></div>
<p class="fb-msg" role="status"></p>
</form>
</div>
<style>
.fb-open{flex:none;font-weight:600;font-size:14px;line-height:1;font-family:inherit;color:var(--accent,var(--green,#0b7a5e));background:#fff;border:1px solid var(--line,#dbe4de);border-radius:6px;padding:9px 13px;cursor:pointer;white-space:nowrap}
.fb-open:hover{border-color:var(--accent,var(--green,#0b7a5e))}.fb-short{display:none}
@media(max-width:560px){.fb-long{display:none}.fb-short{display:inline}.fb-open{padding:8px 10px;font-size:13px}}
.fb-pop{width:min(460px,calc(100vw - 32px));border:1px solid var(--line,#dbe4de);border-radius:8px;padding:22px 24px;background:#fff;color:var(--ink,#1a3029);font-family:inherit;text-align:left}
.fb-pop::backdrop{background:rgba(18,20,23,.28)}
.fb-pop h2{margin:0 0 6px;font-size:21px;letter-spacing:-.01em}.fb-pop p{margin:0 0 14px;font-size:15px;line-height:1.45;color:var(--ink-2,var(--muted,#52675d))}
.fb-pop fieldset{border:0;padding:0;margin:0 0 14px;min-width:0}.fb-pop legend{padding:0;font-weight:600;font-size:14px;margin-bottom:6px}.fb-pop fieldset label{display:inline-flex;align-items:center;gap:6px;margin-right:20px}
.fb-pop label{font-size:15px}.fb-pop label[for]{display:block;font-weight:600;font-size:14px;margin-bottom:6px}
.fb-pop input[type=radio]{accent-color:var(--accent,var(--green,#0b7a5e))}
.fb-pop textarea{display:block;width:100%;box-sizing:border-box;font:inherit;font-size:15px;padding:10px 12px;border:1px solid var(--line,#dbe4de);border-radius:6px;resize:vertical}
.fb-pop .fb-mail input{display:block;width:100%;box-sizing:border-box;font:inherit;font-size:15px;padding:9px 12px;border:1px solid var(--line,#dbe4de);border-radius:6px;margin-bottom:10px}.fb-opt{font-weight:400;color:var(--ink-3,var(--muted,#64756d))}
.fb-pop textarea:focus{outline:2px solid var(--accent,var(--green,#0b7a5e));outline-offset:1px}
.fb-pop .fb-note{font-size:13px;margin:6px 0 16px}.fb-hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}
.fb-act{display:flex;gap:10px}.fb-act button{font-weight:600;font-size:15px;line-height:1;font-family:inherit;padding:10px 16px;border-radius:6px;cursor:pointer;border:1px solid var(--line,#dbe4de);background:#fff;color:var(--ink,#1a3029)}
.fb-act button[type=submit]{background:var(--accent,var(--green,#0b7a5e));border-color:var(--accent,var(--green,#0b7a5e));color:#fff}.fb-act button:disabled{opacity:.6}
.fb-pop .fb-msg{margin:12px 0 0;font-weight:600;color:var(--accent,var(--green,#0b7a5e))}.fb-pop .fb-msg:empty{display:none}
.fb-support{font-weight:600;color:var(--accent,var(--green,#0b7a5e))}
</style>
<script>
(()=>{const p=document.getElementById('fb-pop'),f=p.querySelector('form'),m=p.querySelector('.fb-msg'),b=f.querySelector('[type=submit]');
p.addEventListener('toggle',e=>{if(e.newState==='open'){f.elements.page.value=location.href;f.elements.page_title.value=document.title;m.textContent='';f.elements.text.focus()}});
f.addEventListener('submit',async e=>{e.preventDefault();b.disabled=true;m.textContent='Изпращане…';
try{const r=await fetch(f.action,{method:'POST',body:new URLSearchParams(new FormData(f)),headers:{Accept:'application/json'}}),j=await r.json();m.textContent=j.message;if(r.ok)f.elements.text.value=''}
catch(_){m.textContent='Не успяхме да го изпратим. Опитай пак след малко.'}b.disabled=false})})();
</script>"""

BUTTON = button(mail_on())  # read once at start: the unit sets the env


if __name__ == "__main__":  # the timer: python feedback.py watch <data_dir> <owner/repo>
    import sys
    assert sys.argv[1] == "watch", "usage: feedback.py watch <data_dir> <owner/repo>"
    watch(sys.argv[2], sys.argv[3])
