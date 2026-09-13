"""Brand asset generator for the Base Power integration.

The art is a stylized front view of the actual device - Base's wall-mounted home
battery, the stacked cabinet that appears in their own app render - rather than
their "BASE" wordmark. Two reasons: the wordmark is their trademark and a
generated approximation of one tends to look wrong, and the estate's own
precedent is the device (see ha-tuxedo-touch/brand/README.md).

Drawn programmatically with Pillow, rendered at 4x and downsampled, so the edges
stay clean at 256 px without shipping a hand-drawn master.

The palette has to survive BOTH Home Assistant themes. A white cabinet on
transparency disappears on a light theme and a dark one disappears on dark, so
the body carries its own dark outline and the light panels sit inside it.

    python generate.py            # contact sheet to preview/, nothing shipped
    python generate.py --final    # write the PNGs into custom_components/
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "custom_components", "base_power", "brand")
PREVIEW = os.path.join(HERE, "preview")

SLATE = (38, 45, 56, 255)        # outline / body edge, reads on light themes
PANEL = (242, 244, 246, 255)     # cabinet face, reads on dark themes
PANEL_SHADE = (214, 219, 225, 255)
SEAM = (168, 176, 186, 255)
ENERGY = (0, 176, 132, 255)      # charge bolt
ENERGY_DIM = (0, 140, 106, 255)


def _rounded(draw, box, r, fill, outline=None, width=0):
    draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def draw_battery(size: int) -> Image.Image:
    """The cabinet: three stacked modules, a seam between each, a charge bolt."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Cabinet occupies a tall rectangle centred with margin for the bolt.
    w = int(size * 0.52)
    h = int(size * 0.74)
    x0 = (size - w) // 2
    y0 = (size - h) // 2
    r = int(size * 0.075)
    edge = max(2, int(size * 0.028))

    _rounded(d, [x0, y0, x0 + w, y0 + h], r, PANEL, SLATE, edge)

    # Three modules: seams at the thirds, inset so the outline stays unbroken.
    inset = edge + int(size * 0.012)
    for i in (1, 2):
        y = y0 + int(h * i / 3.0)
        d.line([x0 + inset, y, x0 + w - inset, y], fill=SEAM,
               width=max(1, int(size * 0.012)))

    # A shaded strip down the right gives it depth without a gradient.
    d.rectangle([x0 + w - int(w * 0.22), y0 + edge,
                 x0 + w - edge, y0 + h - edge], fill=PANEL_SHADE)
    _rounded(d, [x0, y0, x0 + w, y0 + h], r, None, SLATE, edge)

    # Wall bracket feet, so it reads as wall-mounted rather than floating.
    foot_w = int(w * 0.18)
    foot_h = max(2, int(size * 0.018))
    for fx in (x0 + int(w * 0.16), x0 + w - int(w * 0.16) - foot_w):
        d.rectangle([fx, y0 + h, fx + foot_w, y0 + h + foot_h], fill=SLATE)

    # Charge bolt, centred on the middle module.
    cx, cy = size // 2, size // 2
    s = size * 0.155
    bolt = [
        (cx + s * 0.18, cy - s),
        (cx - s * 0.62, cy + s * 0.16),
        (cx - s * 0.08, cy + s * 0.16),
        (cx - s * 0.30, cy + s),
        (cx + s * 0.66, cy - s * 0.22),
        (cx + s * 0.06, cy - s * 0.22),
    ]
    d.polygon(bolt, fill=ENERGY, outline=ENERGY_DIM)
    return img


def _font(px: int):
    for name in ("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf", "calibrib.ttf"):
        try:
            return ImageFont.truetype(os.path.join(r"C:\Windows\Fonts", name), px)
        except OSError:
            continue
    return ImageFont.load_default()


def make_icon(master: int = 1024) -> Image.Image:
    return draw_battery(master)


def make_logo(dark: bool = False, master_h: int = 512) -> Image.Image:
    """Device on the left, product name on the right, at 2:1."""
    w, h = master_h * 2, master_h
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    mark = draw_battery(int(h * 0.94)).resize(
        (int(h * 0.94), int(h * 0.94)), Image.LANCZOS)
    img.alpha_composite(mark, (int(h * 0.02), int(h * 0.03)))

    d = ImageDraw.Draw(img)
    fg = (255, 255, 255, 255) if dark else SLATE
    f1 = _font(int(h * 0.235))
    f2 = _font(int(h * 0.155))
    tx = int(h * 1.00)
    d.text((tx, int(h * 0.30)), "Base", font=f1, fill=fg)
    bb = d.textbbox((tx, int(h * 0.30)), "Base", font=f1)
    d.text((bb[2] + int(h * 0.05), int(h * 0.335)), "Power", font=f1, fill=ENERGY)
    d.text((tx, int(h * 0.575)), "HOME BATTERY", font=f2,
           fill=(fg[0], fg[1], fg[2], 165))
    return img


def _save(img: Image.Image, path: str, target_short: int) -> None:
    short = min(img.size)
    scale = target_short / short
    out = img.resize((max(1, round(img.width * scale)),
                      max(1, round(img.height * scale))), Image.LANCZOS)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.save(path, optimize=True)
    print("   %-46s %d x %d" % (os.path.relpath(path, HERE), out.width, out.height))


def final() -> None:
    icon = make_icon()
    _save(icon, os.path.join(OUT, "icon.png"), 256)
    _save(icon, os.path.join(OUT, "icon@2x.png"), 512)
    logo = make_logo(dark=False)
    _save(logo, os.path.join(OUT, "logo.png"), 256)
    _save(logo, os.path.join(OUT, "logo@2x.png"), 512)
    dark = make_logo(dark=True)
    _save(dark, os.path.join(OUT, "dark_logo.png"), 256)
    _save(dark, os.path.join(OUT, "dark_logo@2x.png"), 512)


def sheet() -> None:
    """Contact sheet on both theme backgrounds, because that is the real test."""
    os.makedirs(PREVIEW, exist_ok=True)
    tiles = [("icon", make_icon().resize((256, 256), Image.LANCZOS)),
             ("logo", make_logo(False).resize((512, 256), Image.LANCZOS)),
             ("dark_logo", make_logo(True).resize((512, 256), Image.LANCZOS))]
    w = 256 + 512 + 512 + 64
    s = Image.new("RGBA", (w, 560), (255, 255, 255, 255))
    d = ImageDraw.Draw(s)
    d.rectangle([0, 280, w, 560], fill=(24, 26, 30, 255))
    x = 16
    for _, im in tiles:
        s.alpha_composite(im, (x, 16))
        s.alpha_composite(im, (x, 296))
        x += im.width + 16
    p = os.path.join(PREVIEW, "contact_sheet.png")
    s.save(p, optimize=True)
    print("   preview: %s" % p)


if __name__ == "__main__":
    if "--final" in sys.argv:
        final()
    else:
        sheet()
