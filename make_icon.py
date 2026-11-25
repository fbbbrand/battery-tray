"""Generate assets/battery_tray.ico (exe icon): headset glyph with a battery bar."""
from pathlib import Path

from PIL import Image, ImageDraw

from icons import GREEN, _draw_headset

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
_draw_headset(d, (235, 235, 235, 255), (28, 20, 228, 170))
# battery bar under the headset
d.rounded_rectangle((48, 192, 196, 236), radius=10, outline=(235, 235, 235, 255), width=10)
d.rectangle((196, 204, 210, 224), fill=(235, 235, 235, 255))
d.rounded_rectangle((62, 206, 150, 222), radius=4, fill=GREEN)

out = Path(__file__).with_name("assets")
out.mkdir(exist_ok=True)
img.save(out / "battery_tray.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
img.save(out / "battery_tray.png")
print("ok")
