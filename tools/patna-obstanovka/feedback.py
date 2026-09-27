"""Обратна връзка: the rightmost header button on every page files an issue in the tool's own GitHub repo.

The same file is copied unchanged into every Prozrachnost tool (docs/plans/STANDARD.md, "Обратна връзка").
An app does three things:
    app.include_router(feedback.router("DenislavDenev/<repo>", spool_dir))
    feedback.BUTTON                  -> last element of the header
    feedback.support_link(hub_url)   -> last element of the footer ("Подкрепи проекта", the hub's /podkrepi)
The token is GITHUB_ISSUES_TOKEN (fine-grained, Issues read/write only), from /etc/prozrachnost/feedback.env.
Nothing is lost: a message goes to the spool first and leaves it only when GitHub has created the issue.
The sender's IP is used for the hourly limit in memory and never written anywhere.
"""
import json
import os
import re
import threading
import time
import urllib.request
from datetime import datetime
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

KINDS = {"problem": ("Проблем", "bug"), "idea": ("Предложение", "enhancement")}
LABEL = "обратна връзка"
MAX_TEXT = 4000
PER_CLIENT, PER_HOUR = 5, 30  # messages an hour: from one sender, from everyone (per worker)
URL = re.compile(r"https?://[^\s<>()\[\]|`\"']{1,500}")
_lock = threading.Lock()
_hits: dict[str, list[float]] = {}


def issue(kind, text, page="", page_title="", agent="", now=None):
    """The GitHub issue for one message: a titled quote of the text, then where and when it was sent."""
    name, label = KINDS[kind]
    text = text.strip().replace("@", "@​")  # no @mentions: a sender must not ping anyone on GitHub
    line = " ".join(text.split())
    title = f"{name}: {line[:80]}{'…' if len(line) > 80 else ''}"
    quote = "\n".join(f"> {l}" if l.strip() else ">" for l in text.splitlines())
    page = page if URL.fullmatch(page or "") else ""
    shown = " ".join(re.sub(r"[\[\]`|\\]", "", page_title or "").split())[:150] or page
    when = (now or datetime.now(ZoneInfo("Europe/Sofia"))).strftime("%d.%m.%Y, %H:%M")
    meta = [f"**Страница:** [{shown}]({page})" if page else "**Страница:** не е посочена",
            f"**Изпратено:** {when} (София)"]
    agent = re.sub(r"[`\r\n]", "", agent or "")[:200]
    if agent:
        meta.append(f"**Браузър:** `{agent}`")
    body = (f"### {name}\n\n{quote}\n\n---\n\n" + "  \n".join(meta)
            + "\n\n<sub>Изпратено с бутона „Обратна връзка“ на сайта. IP адресът на подателя не се пази.</sub>\n")
    return {"title": title, "body": body, "labels": [label, LABEL]}


def post(repo, payload):
    """Create the issue; True only when GitHub answers 201."""
    token = os.getenv("GITHUB_ISSUES_TOKEN")
    if not token:
        return False
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/issues", method="POST",
                                 data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Prozrachnost-feedback"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 201
    except OSError as e:
        print(f"feedback: GitHub did not take the issue for {repo}: {e}", flush=True)
        return False


def flush(spool: Path, repo, payload=None, send=None):
    """Append payload (if any) to the spool, send everything in it, keep what GitHub did not take.
    Returns how many are still waiting."""
    send = send or post
    spool.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(spool, "a+", encoding="utf-8") as f:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)  # two uvicorn workers share one spool
        if payload:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            f.flush()
        f.seek(0)
        left = [l for l in f.read().splitlines() if l.strip() and not send(repo, json.loads(l))]
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


def accept(form, client, referer, agent, spool, repo, send=None):
    """One submitted form -> (HTTP status, message for the sender)."""
    if form.get("website"):  # the hidden field only bots fill in
        return 200, "Благодарим! Получихме го."
    kind, text = form.get("kind", ""), (form.get("text") or "").strip()
    if kind not in KINDS or not 5 <= len(text) <= MAX_TEXT:
        return 400, f"Избери вид и напиши между 5 и {MAX_TEXT} знака."
    if not allowed(client):
        return 429, "Получихме много съобщения за последния час. Опитай пак по-късно."
    flush(spool, repo, issue(kind, text, form.get("page") or referer, form.get("page_title", ""), agent), send)
    return 200, "Благодарим! Получихме го и ще го прегледаме."


def router(repo, spool_dir):
    spool = Path(spool_dir) / "feedback-pending.jsonl"
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
                                              request.headers.get("user-agent", ""), spool, repo)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"ok": status == 200, "message": msg}, status_code=status)
        back = escape(request.headers.get("referer") or "/")
        return HTMLResponse(f'<!doctype html><meta charset="utf-8"><title>Обратна връзка</title>'
                            f'<p>{escape(msg)}</p><p><a href="{back}">Обратно към страницата</a></p>', status_code=status)

    return r


if __name__ == "__main__":  # send what is waiting: python feedback.py <spool file> <owner/repo>
    import sys
    print("still waiting:", flush(Path(sys.argv[1]), sys.argv[2]))


def support_link(hub_url):
    return f'<a class="fb-support" href="{escape(hub_url.rstrip("/"))}/podkrepi">Подкрепи проекта</a>'


BUTTON = """<button type="button" class="fb-open" popovertarget="fb-pop" aria-label="Обратна връзка: проблем или предложение"><span class="fb-long">Обратна връзка</span><span class="fb-short">Пиши ни</span></button>
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
