"""Generate the original SH Download Manager icon set."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)


def rounded_gradient(size: int) -> Image.Image:
    scale = 4
    img = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    # vertical gradient from vivid blue to deep navy
    top = (37, 133, 255)
    bottom = (9, 58, 148)
    grad = Image.new("RGBA", (1, size * scale))
    for y in range(size * scale):
        t = y / max(1, size * scale - 1)
        grad.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    grad = grad.resize((size * scale, size * scale))
    mask = Image.new("L", (size * scale, size * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size * scale - 1, size * scale - 1], radius=int(size * scale * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)
    # subtle top highlight on a separate layer so alpha blends instead of replacing pixels
    hi = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(hi).rounded_rectangle(
        [int(size * scale * 0.04), int(size * scale * 0.04), int(size * scale * 0.96), int(size * scale * 0.5)],
        radius=int(size * scale * 0.18),
        fill=(255, 255, 255, 10),
    )
    img = Image.alpha_composite(img, hi)
    return img.resize((size, size), Image.LANCZOS)


def make_icon(size: int) -> Image.Image:
    img = rounded_gradient(size)
    d = ImageDraw.Draw(img)
    s = size
    # white download tray + arrow
    cx = s / 2
    shaft_w = max(2, int(s * 0.105))
    shaft_top = int(s * 0.20)
    shaft_bottom = int(s * 0.53)
    d.rounded_rectangle(
        [cx - shaft_w / 2, shaft_top, cx + shaft_w / 2, shaft_bottom],
        radius=shaft_w / 2,
        fill=(255, 255, 255, 255),
    )
    head_w = int(s * 0.34)
    head_h = int(s * 0.20)
    d.polygon(
        [
            (cx - head_w / 2, shaft_bottom - 1),
            (cx + head_w / 2, shaft_bottom - 1),
            (cx, shaft_bottom + head_h),
        ],
        fill=(255, 255, 255, 255),
    )
    tray_y = int(s * 0.74)
    tray_w = int(s * 0.46)
    tray_h = max(2, int(s * 0.075))
    d.rounded_rectangle(
        [cx - tray_w / 2, tray_y, cx + tray_w / 2, tray_y + tray_h],
        radius=tray_h / 2,
        fill=(255, 255, 255, 235),
    )
    return img


sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
imgs = [make_icon(s) for s in sizes]
imgs[-1].save(ASSETS / "icon.png")
imgs[-1].save(ASSETS / "icon.ico", sizes=[(s, s) for s in sizes])
for s in (16, 32, 48, 128):
    make_icon(s).save(ASSETS / f"ext_{s}.png")
print("wrote", ASSETS / "icon.ico")
