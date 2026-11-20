"""Draws the application icon and writes it in the formats the installers need.

    python packaging/make_icons.py

Outputs: packaging/icons/icon.png (1024 px), icon.ico (Windows), icon.icns (macOS), app/ui/icon.png (256 px), app/ui/icon.ico,
site/assets/icon-512.png. The same design as the inline SVG in app/ui/index.html and site/assets/logo.svg."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
BLUE, RED, WHITE = (42, 120, 214, 255), (208, 59, 59, 255), (255, 255, 255, 255)


def draw(size: int = 1024) -> Image.Image:
    s = 4 * size                      # supersampling for smooth edges
    u = s / 32                        # the SVG uses a 32-unit grid
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([3 * u, 2 * u, 23 * u, 28 * u], radius=3 * u, fill=BLUE)
    for y, w in ((8, 12), (13, 9), (18, 11)):
        d.rounded_rectangle([7 * u, y * u, (7 + w) * u, (y + 2.4) * u], radius=1.2 * u, fill=WHITE)
    cx, cy, r, sw = 22 * u, 21 * u, 6.5 * u, 3 * u
    d.ellipse([cx - r - sw, cy - r - sw, cx + r + sw, cy + r + sw], fill=WHITE)      # halo so the ring reads on blue
    d.ellipse([cx - r - sw / 2, cy - r - sw / 2, cx + r + sw / 2, cy + r + sw / 2], outline=RED, width=int(sw))
    d.line([26.5 * u, 25.5 * u, 30 * u, 29 * u], fill=RED, width=int(sw))
    d.ellipse([30 * u - sw / 2, 29 * u - sw / 2, 30 * u + sw / 2, 29 * u + sw / 2], fill=RED)
    return img.resize((size, size), Image.LANCZOS)


def main():
    out = ROOT / "packaging" / "icons"
    out.mkdir(parents=True, exist_ok=True)
    big = draw(1024)
    big.save(out / "icon.png")
    # BMP frames rather than PNG ones: .NET's System.Drawing.Icon (the pywebview window icon on Windows) reads them reliably.
    big.save(out / "icon.ico", sizes=ICO_SIZES, bitmap_format="bmp")
    big.save(ROOT / "app" / "ui" / "icon.ico", sizes=ICO_SIZES, bitmap_format="bmp")
    big.save(out / "icon.icns")
    big.resize((256, 256), Image.LANCZOS).save(ROOT / "app" / "ui" / "icon.png")
    site = ROOT / "site" / "assets"
    site.mkdir(parents=True, exist_ok=True)
    big.resize((512, 512), Image.LANCZOS).save(site / "icon-512.png")
    big.resize((180, 180), Image.LANCZOS).save(site / "apple-touch-icon.png")
    print("icons written to", out)


if __name__ == "__main__":
    main()
