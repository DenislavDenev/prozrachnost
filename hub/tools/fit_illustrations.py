"""Bring every card illustration to one scale: crop to the drawing, resize it to the same visual size, sit it on one ground line.

Run after adding or replacing a file in static/illustrations/. Rerunning is safe: an already fitted file is left alone.
"""
from pathlib import Path

from PIL import Image

DIR = Path(__file__).resolve().parent.parent / "static" / "illustrations"
W, H = 1200, 720
MAX_W, MAX_H = 1160, 684  # the drawing never goes closer than 20 px to the sides, 28 px to the top
BASE = 712                # bottom of every drawing (its ground line) sits here, just above the card edge
AREA = 0.74 * W * H       # same visual size: equal bounding-box area, unless that breaks MAX_W/MAX_H


def fit(img):
    img = img.convert("RGBA")
    box = img.getchannel("A").point(lambda a: 255 if a > 20 else 0).getbbox()
    art = img.crop(box)
    w, h = art.size
    s = min(MAX_W / w, MAX_H / h, (AREA / (w * h)) ** 0.5)
    if abs(s - 1) < 0.02:  # right size already: only move it, no resampling
        if box[3] in range(BASE - 2, BASE + 3):
            return None
        s = 1
    else:
        art = art.resize((round(w * s), round(h * s)), Image.LANCZOS)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(art, ((W - art.width) // 2, BASE - art.height))
    return out


if __name__ == "__main__":
    for f in sorted(DIR.glob("*.webp")):
        out = fit(Image.open(f))
        if out:
            out.save(f, "WEBP", quality=88, method=6)
            print("fitted", f.name, f.stat().st_size // 1024, "KB")
