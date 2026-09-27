"""Bring every card illustration to one scale: crop to the drawing, drop its ground line, resize it to the same
visual size and stand it on the bottom edge (the card edge is the ground).

Run after adding or replacing a file in static/illustrations/. Rerunning is safe: a fitted file touches the bottom
edge and is left alone.
"""
from pathlib import Path

from PIL import Image

DIR = Path(__file__).resolve().parent.parent / "static" / "illustrations"
W, H = 1200, 720
MAX_W, MAX_H = 1110, 692  # at least 45 px from the sides (clear of the card's round corners), 28 px from the top
AREA = 0.74 * W * H       # same visual size: equal bounding-box area, unless that breaks MAX_W/MAX_H


def opaque(img, y, alpha=20):
    return sum(1 for x in range(img.width) if img.getpixel((x, y))[3] > alpha)


def ground_line_top(img, box):
    """y where the thin light grey ground line starts, or box bottom if there is none.

    The line is the widest block of rows at the bottom; the row above it is much narrower (feet, legs, a wall)."""
    rows = [opaque(img, y) for y in range(box[3] - 1, max(box[1], box[3] - 14), -1)]
    ref = max(rows[:4])
    k = next((i for i in range(1, len(rows)) if rows[i] < 0.9 * ref), None)
    if not k:
        return box[3]
    grey = total = 0
    for y in range(box[3] - k, box[3]):
        for x in range(box[0], box[2]):
            r, g, b, a = img.getpixel((x, y))
            if a > 20:
                total += 1
                grey += min(r, g, b) > 190 and max(r, g, b) - min(r, g, b) < 25
    if grey <= 0.6 * total:
        return box[3]
    top = box[3] - k
    while top > box[3] - k - 2 and opaque(img, top - 1, 0) >= 0.9 * ref:  # the faint anti-aliased top edge of the line
        top -= 1
    return top


def fit(img):
    img = img.convert("RGBA")
    box = img.getchannel("A").point(lambda a: 255 if a > 20 else 0).getbbox()
    if box[3] == H:
        return None  # already stands on the edge
    art = img.crop((box[0], box[1], box[2], ground_line_top(img, box)))
    w, h = art.size
    s = min(MAX_W / w, MAX_H / h, (AREA / (w * h)) ** 0.5)
    art = art.resize((round(w * s), round(h * s)), Image.LANCZOS)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(art, ((W - art.width) // 2, H - art.height))
    return out


if __name__ == "__main__":
    for f in sorted(DIR.glob("*.webp")):
        out = fit(Image.open(f))
        if out:
            out.save(f, "WEBP", quality=88, method=6)
            print("fitted", f.name, f.stat().st_size // 1024, "KB")
