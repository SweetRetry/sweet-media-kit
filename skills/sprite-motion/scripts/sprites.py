#!/usr/bin/env python3
"""Turn generated sprite sheets into clean frames and tileable layers. Needs numpy and Pillow.

  sprites.py row SHEET NAME --frames N [--out DIR]     one row of frames, split on column gaps
  sprites.py grid SHEET NAME --frames N [--out DIR]    rows first, then cells (4x4, 5x5 ...)
  sprites.py layer IMAGE OUT [--strip] [--vertical]    seamless tile along the scroll direction;
                                                       --strip keys the magenta off a partial layer
                                                       (and, horizontally, crops above its top row)
  sprites.py cycle FRAME [FRAME ...]                   frame-to-frame change of an animation loop
  sprites.py contact OUT IMAGE [IMAGE ...]             preview sheet on a dark background

Sheets are expected on a flat #FF00FF background. Background = magenta-ish pixels connected to
the border plus enclosed pure magenta, so purple details inside a sprite survive. An alpha
channel already present in the file is honoured. Frames of one sheet share its vertical extent
(row sheets) or their row's extent (grids), so feet stay on one baseline through a cycle.
"""
import argparse
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageDraw
except ImportError:  # keep -h usable; commands report the setup line instead
    np = Image = ImageDraw = None

MIN_GAP, MIN_WIDTH, RIM = 12, 10, 3


def key(im):
    """RGBA array with the magenta background removed, its antialiased rim eaten and despilled."""
    own = np.asarray(im.convert("RGBA"))[..., 3] if im.mode == "RGBA" else None
    a = np.asarray(im.convert("RGB")).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    loose = (r - g > 80) & (b - g > 80) & (abs(r - b) < 90)
    strict = (r > 215) & (b > 215) & (g < 60)
    m = Image.fromarray(np.where(loose, 255, 0).astype(np.uint8))
    h, w = loose.shape
    for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if m.getpixel((x, y)) == 255:
            ImageDraw.floodfill(m, (x, y), 128)
    bg = (np.asarray(m) == 128) | strict
    tinted = (r > g) & (b > g)
    for _ in range(RIM):
        grown = bg.copy()
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            grown |= np.roll(bg, d, axis=(0, 1)) & tinted
        bg = grown
    near = np.zeros_like(bg)
    for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        near |= np.roll(bg, d, axis=(0, 1))
    out = a.copy()
    spill = near & ~bg & tinted
    out[..., 2] = np.where(spill, np.minimum(b, g + (r - g) // 3), b)
    if own is not None:
        bg |= own < 128
    return np.dstack([out.astype(np.uint8), np.where(bg, 0, 255).astype(np.uint8)])


def spans(mask, axis):
    """Occupied column (axis=0) or row (axis=1) spans separated by gaps wider than MIN_GAP."""
    idx = np.where(mask.any(axis=axis))[0]
    if not len(idx):
        return []
    out, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > MIN_GAP:
            out.append((start, prev + 1))
            start = i
        prev = i
    out.append((start, prev + 1))
    return [s for s in out if s[1] - s[0] >= MIN_WIDTH]


def save_frames(cells, rgba, name, out, n):
    if len(cells) != n:
        sys.exit(f"{name}: expected {n} frames, found {len(cells)}. Regenerate the sheet or adjust --frames; "
                 "merged frames mean the gaps were too narrow.")
    h = max(y1 - y0 for y0, y1, _, _ in cells)
    w = max(x1 - x0 for _, _, x0, x1 in cells)
    Path(out).mkdir(parents=True, exist_ok=True)
    for i, (y0, y1, x0, x1) in enumerate(cells, 1):
        part = rgba[y0:y1, x0:x1]
        canvas = np.zeros((h, w, 4), np.uint8)
        left = (w - part.shape[1]) // 2
        canvas[h - part.shape[0]:, left:left + part.shape[1]] = part
        Image.fromarray(canvas).save(Path(out) / f"{name}_{i}.png")
    print(f"{name}: {n} frames, canvas {w}x{h} -> {out}")


def cmd_row(a):
    rgba = key(Image.open(a.sheet))
    solid = rgba[..., 3] > 0
    rows = np.where(solid.any(axis=1))[0]
    y0, y1 = rows[0], rows[-1] + 1
    save_frames([(y0, y1, x0, x1) for x0, x1 in spans(solid, 0)], rgba, a.name, a.out, a.frames)


def cmd_grid(a):
    rgba = key(Image.open(a.sheet))
    solid = rgba[..., 3] > 0
    cells = []
    for y0, y1 in spans(solid, 1):
        cells += [(y0, y1, x0, x1) for x0, x1 in spans(solid[y0:y1], 0)]
    save_frames(cells, rgba, a.name, a.out, a.frames)


def seamless(a, band=320, blend=48):
    """Crop between the best-matching columns near each end, then wrap-blend the seam."""
    x = a[..., :3].astype(int)
    best = min((np.abs(x[:, l] - x[:, r]).mean(), l, r)
               for l in range(0, band, 2) for r in range(a.shape[1] - band, a.shape[1], 2))
    tile = a[:, best[1]:best[2]].astype(float)
    w = tile.shape[1] - blend
    ramp = np.linspace(0, 1, blend)[None, :, None]
    out = tile[:, :w].copy()
    out[:, :blend] = tile[:, w:] * (1 - ramp) + tile[:, :blend] * ramp
    print(f"seam columns {best[1]}:{best[2]}, residual {best[0]:.1f} (adjacent columns differ ~1)")
    if best[0] > 12:
        print("warning: residual well above ~10, the source does not tile in this direction; regenerate it")
    return out.round().astype(np.uint8)


def cmd_layer(a):
    im = Image.open(a.image)
    if a.strip:
        arr = key(im)
        if not a.vertical:
            top = np.where(arr[..., 3].any(axis=1))[0][0]
            arr = arr[top:]
            print(f"strip starts at source row {top}")
    else:
        arr = np.asarray(im.convert("RGB"))
    if a.vertical:  # seamless() works on columns; turn rows into columns and back
        tile = Image.fromarray(np.swapaxes(seamless(np.swapaxes(arr, 0, 1)), 0, 1))
    else:
        tile = Image.fromarray(seamless(arr))
    tile.save(a.out)
    t = tile.convert("RGBA")
    check = Path(a.out).with_name(Path(a.out).stem + "_seamcheck.png")
    if a.vertical:
        twice = Image.new("RGBA", (t.width, t.height * 2), (30, 30, 46, 255))
        twice.alpha_composite(t, (0, 0))
        twice.alpha_composite(t, (0, t.height))
        twice.crop((0, t.height - 500, t.width, t.height + 500)).save(check)
    else:
        twice = Image.new("RGBA", (t.width * 2, t.height), (30, 30, 46, 255))
        twice.alpha_composite(t, (0, 0))
        twice.alpha_composite(t, (t.width, 0))
        twice.crop((t.width - 500, 0, t.width + 500, t.height)).save(check)
    print(f"{a.out} {tile.size}; look at {check}: the seam sits in the middle")


def cmd_cycle(a):
    fr = [np.asarray(Image.open(f).convert("RGBA")).astype(float) for f in a.frames]
    h, w = min(f.shape[0] for f in fr), min(f.shape[1] for f in fr)
    fr = [f[-h:, (f.shape[1] - w) // 2:(f.shape[1] - w) // 2 + w] for f in fr]
    d = [np.abs(fr[i] - fr[(i + 1) % len(fr)]).mean() for i in range(len(fr))]
    mean = float(np.mean(d))
    print("step diffs (last = wrap to first):", " ".join(f"{x:.1f}" for x in d))
    print(f"mean {mean:.1f}  max/mean {max(d) / mean:.2f}  min/mean {min(d) / mean:.2f}")
    print("Smooth loop: low mean, max/mean under ~1.6, wrap step like the others. "
          "A near-zero step is a duplicate pose; a spike is a pop.")


def cmd_contact(a):
    ims = [Image.open(f).convert("RGBA") for f in a.images]
    cell = 220
    cols = min(8, len(ims))
    sheet = Image.new("RGB", (cols * cell, ((len(ims) - 1) // cols + 1) * cell), (30, 30, 46))
    for i, im in enumerate(ims):
        k = (cell - 10) / max(im.size)
        im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.NEAREST)
        sheet.paste(im, ((i % cols) * cell + (cell - im.width) // 2, (i // cols) * cell + (cell - im.height) // 2), im)
    sheet.save(a.out)
    print(a.out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("row", "grid"):
        p = sub.add_parser(name)
        p.add_argument("sheet")
        p.add_argument("name")
        p.add_argument("--frames", type=int, required=True)
        p.add_argument("--out", default="assets")
    p = sub.add_parser("layer")
    p.add_argument("image")
    p.add_argument("out")
    p.add_argument("--strip", action="store_true")
    p.add_argument("--vertical", action="store_true", help="tile top-to-bottom instead of left-to-right")
    p = sub.add_parser("cycle")
    p.add_argument("frames", nargs="+")
    p = sub.add_parser("contact")
    p.add_argument("out")
    p.add_argument("images", nargs="+")
    a = ap.parse_args()
    if np is None:
        sys.exit("needs numpy and Pillow: python3 -m venv .venv && .venv/bin/pip install numpy pillow, "
                 "then run this script with .venv/bin/python")
    {"row": cmd_row, "grid": cmd_grid, "layer": cmd_layer, "cycle": cmd_cycle, "contact": cmd_contact}[a.cmd](a)


if __name__ == "__main__":
    main()
