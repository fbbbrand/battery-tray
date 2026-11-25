"""Tray icon rendering: device glyph on top, battery percentage below."""
from __future__ import annotations

import winreg

from PIL import Image, ImageDraw, ImageFont

SIZE = 64  # rendered large, Windows scales it down to the tray size
FONT_PATH = r"C:\Windows\Fonts\segoeuib.ttf"

GREEN = (80, 220, 120, 255)
ORANGE = (255, 170, 40, 255)
RED = (255, 75, 75, 255)
GREY = (140, 140, 140, 255)


def taskbar_is_light() -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(key, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


def _fg() -> tuple:
    return (20, 20, 20, 255) if taskbar_is_light() else (245, 245, 245, 255)


def _draw_headset(d: ImageDraw.ImageDraw, color, box):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    lw = max(3, w // 9)
    # headband arc
    d.arc((x0 + lw // 2, y0, x1 - lw // 2, y0 + h * 1.6), 180, 360, fill=color, width=lw)
    # ear cups
    cw, ch = w * 0.28, h * 0.55
    d.rounded_rectangle((x0, y1 - ch, x0 + cw, y1), radius=cw / 3, fill=color)
    d.rounded_rectangle((x1 - cw, y1 - ch, x1, y1), radius=cw / 3, fill=color)


def _draw_mouse(d: ImageDraw.ImageDraw, color, box):
    x0, y0, x1, y1 = box
    w = x1 - x0
    # narrower body, centered
    bx0, bx1 = x0 + w * 0.25, x1 - w * 0.25
    lw = max(3, int(w // 10))
    d.rounded_rectangle((bx0, y0, bx1, y1), radius=(bx1 - bx0) / 2, outline=color, width=lw)
    cx = (bx0 + bx1) / 2
    d.line((cx, y0 + 2, cx, y0 + (y1 - y0) * 0.42), fill=color, width=lw)  # button split


def _draw_keyboard(d: ImageDraw.ImageDraw, color, box):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    lw = max(3, int(w // 12))
    top = y0 + h * 0.2
    d.rounded_rectangle((x0, top, x1, y1), radius=h / 6, outline=color, width=lw)
    key = w / 9
    for row, y in enumerate((top + h * 0.22, top + h * 0.45)):
        for i in range(4):
            kx = x0 + w * 0.15 + i * key * 1.9
            d.rectangle((kx, y, kx + key, y + key * 0.7), fill=color)
    d.rectangle((x0 + w * 0.3, y1 - h * 0.25, x1 - w * 0.3, y1 - h * 0.15), fill=color)


DRAWERS = {"headset": _draw_headset, "keyboard": _draw_keyboard}


def level_color(percent: int | None, charging: bool, stale: bool) -> tuple:
    if percent is None or stale:
        return GREY
    if charging:
        return GREEN
    if percent <= 15:
        return RED
    if percent <= 30:
        return ORANGE
    return _fg()


def render(kind: str, percent: int | None, charging: bool = False, stale: bool = False) -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    glyph_color = GREY if percent is None or stale else _fg()
    glyph_box = (16, 1, 48, 25)
    DRAWERS.get(kind, _draw_mouse)(d, glyph_color, glyph_box)

    text = "–" if percent is None else str(percent)
    size = 40 if len(text) <= 2 else 30
    font = ImageFont.truetype(FONT_PATH, size)
    color = level_color(percent, charging, stale)
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    x = (SIZE - (r - l)) / 2 - l
    y = SIZE - (b - t) - t - 1
    d.text((x, y), text, font=font, fill=color)

    if charging and percent is not None:
        # small lightning bolt, top-right corner
        bolt = [(58, 0), (50, 13), (55, 13), (51, 24), (62, 9), (57, 9), (61, 0)]
        d.polygon(bolt, fill=GREEN)
    return img


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    samples = [("headset", 67, False, False), ("mouse", 40, False, False),
               ("headset", 100, True, False), ("mouse", 12, False, False),
               ("mouse", 40, False, True), ("headset", None, False, False)]
    sheet = Image.new("RGBA", (len(samples) * 80, 3 * 80), (32, 32, 32, 255))
    for i, s in enumerate(samples):
        icon = render(*s)
        for j, px in enumerate((64, 32, 24)):
            small = icon.resize((px, px), Image.LANCZOS)
            sheet.alpha_composite(small, (i * 80 + (80 - px) // 2, j * 80 + (80 - px) // 2))
    sheet.save(f"{out}/preview.png")
    print("saved")
