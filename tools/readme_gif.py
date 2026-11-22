#!/usr/bin/env python3
"""A GIF from the frames of a scene of tools/readme_pictures.py: the window on a soft
background, held on each frame, faded from one to the next.

    python tools/readme_gif.py FRAMES SCENE OUT.gif HOLD...   (one hold in seconds a frame)
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

FRAMES, SCENE, OUT = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
HOLDS = [float(h) for h in sys.argv[4:]]
BG, WIDTH, PAD = (240, 238, 234), 520, 24


def card(path: Path) -> Image.Image:
    win = Image.open(path).convert("RGBA")
    win = win.resize((WIDTH, round(win.height * WIDTH / win.width)), Image.LANCZOS)
    mask = Image.new("L", win.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, win.width - 1, win.height - 1], radius=12, fill=255)
    out = Image.new("RGB", (WIDTH + 2 * PAD, win.height + 2 * PAD), BG)
    shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([PAD, PAD + 4, PAD + WIDTH, PAD + win.height + 4], radius=14,
                                             fill=(0, 0, 0, 55))
    out.paste(shadow.filter(ImageFilter.GaussianBlur(9)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(9)))
    out.paste(win, (PAD, PAD), mask)
    return out


paths = sorted(FRAMES.glob(f"{SCENE}-*.png"))
assert len(paths) == len(HOLDS), (len(paths), len(HOLDS))
cards = [card(p) for p in paths]
frames, durations = [], []
for k, (c, hold) in enumerate(zip(cards, HOLDS, strict=True)):
    if k and hold >= 1.0:          # a step of the work: faded in; the meters' quick frames cut
        for t in (0.25, 0.5, 0.75):
            frames.append(Image.blend(cards[k - 1], c, t))
            durations.append(60)
    frames.append(c)
    durations.append(round(hold * 1000))
palette = Image.new("RGB", (cards[0].width, cards[0].height * len(cards)))
for k, c in enumerate(cards):
    palette.paste(c, (0, k * c.height))
palette = palette.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
quantised = [f.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG) for f in frames]
quantised[0].save(OUT, save_all=True, append_images=quantised[1:], duration=durations, loop=0, optimize=True)
print(OUT, len(frames), f"{OUT.stat().st_size // 1024} KB")
