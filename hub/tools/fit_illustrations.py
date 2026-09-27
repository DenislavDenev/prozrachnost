"""Bring every card illustration to one scale: crop to the drawing, resize it to the same visual size, sit it on one ground line.

Run after adding or replacing a file in static/illustrations/. Rerunning is safe: an already fitted file is left alone.
"""
from pathlib import Path

from PIL import Image

DIR = Path(__file__).resolve().parent.parent / "static" / "illustrations"
W, H = 1200, 720
MAX_W, MAX_H = 1130, 660  # the drawing never goes closer than 35 px to the sides, 30 px to the top
BASE = 700                # bottom of every drawing (its ground line) sits here
AREA = 0.62 * W * H       # same visual size: equal bounding-box area, unless that breaks MAX_W/MAX_H


def fit(img):
    img = img.convert("RGBA")
    box = img.getchannel("A").point(lambda a: 255 if a > 20 else 0).getbbox()
    art = img.crop(box)
    w, h = art.size
    s = min(MAX_W / w, MAX_H / h, (AREA / (w * h)) ** 0.5)
    nw, nh = round(w * s), round(h * s)
    if abs(s - 1) < 0.02 and box[3] in range(BASE - 2, BASE + 3):
        return None  # already fitted
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(art.resize((nw, nh), Image.LANCZOS), ((W - nw) // 2, BASE - nh))
    return out


if __name__ == "__main__":
    for f in sorted(DIR.glob("*.webp")):
        out = fit(Image.open(f))
        if out:
            out.save(f, "WEBP", quality=88, method=6)
            print("fitted", f.name, f.stat().st_size // 1024, "KB")
