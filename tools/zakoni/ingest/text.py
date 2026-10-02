"""Text of the source: HTML escaped once or twice, non-breaking spaces, tags. The original is kept next to the plain text."""
import datetime as dt
import html
import re

_BLOCK = re.compile(r"<\s*(?:br|/p|/li|/div|/h[1-6]|/tr)\b[^>]*>", re.I)
_TAG = re.compile(r"</?[A-Za-z][^>]*>")


_MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def scrub(s):
    """E-mail addresses are never kept (the portal's texts name the mailbox where proposals go, sometimes a person's)."""
    return None if s is None else _MAIL.sub("[имейл]", s)


def plain(s):
    """&lt;p&gt;A&amp;nbsp;B&lt;/p&gt; -> 'A B'. Escaped text is unescaped first, then the tags go, then what was escaped inside."""
    if s is None:
        return None
    t = html.unescape(scrub(s))
    if "<" in t:
        t = _TAG.sub("", _BLOCK.sub("\n", t))
    t = html.unescape(t) if "&" in t else t
    t = t.replace("\xa0", " ").replace("​", "")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln) or None


def one_line(s):
    p = plain(s)
    return None if p is None else re.sub(r"\s+", " ", p)


def iso(s, what="дата"):
    """YYYY-MM-DD -> date; anything else is a ShapeError of the caller's (ValueError here)."""
    try:
        return dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        raise ValueError(f"{what}: {s!r} не е ГГГГ-ММ-ДД") from None


def sentinel(d):
    """The sources write 9999-01-01 for 'no end' (or an unknown date). -> None for such a date."""
    return None if d is not None and d.year >= 9000 else d
