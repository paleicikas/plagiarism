"""Draws site/assets/og-image.png (1200x630 link preview) from the icon and the English progress screenshot.

    python packaging/make_og_image.py      (needs Pillow; run after updating site/assets/screens/en/progress.png)

Same look as the app and the site: soft colour field, glass frame, blue-violet accent. Uses Inter if it is
installed, otherwise Segoe UI / DejaVu / Arial.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "site" / "assets"
W, H = 1200, 630
INK, MUTED, BG = "#171a20", "#5d6370", "#eef1f8"


def font(size: int, weight: str = "regular"):
    names = {"regular": ["Inter-Regular.ttf", "segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"],
             "bold": ["Inter-Bold.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"],
             "black": ["Inter-ExtraBold.ttf", "Inter-Bold.ttf", "seguibl.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"]}[weight]
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


def gradient(size, stops):
    """Horizontal gradient image through the given (position, (r, g, b)) stops."""
    w, h = size
    g = Image.new("RGB", (w, 1))
    for x in range(w):
        t = x / max(w - 1, 1)
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= t <= p1:
                k = (t - p0) / (p1 - p0)
                g.putpixel((x, 0), tuple(round(a + (b - a) * k) for a, b in zip(c0, c1)))
                break
    return g.resize((w, h))


def gradient_text(img, xy, text, fnt, stops):
    d = ImageDraw.Draw(img)
    box = d.textbbox(xy, text, font=fnt)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).text(xy, text, font=fnt, fill=255)
    fill = Image.new("RGB", img.size)
    fill.paste(gradient((box[2] - box[0], box[3] - box[1]), stops), (box[0], box[1]))
    img.paste(fill, (0, 0), mask)


def main():
    img = Image.new("RGB", (W, H), BG)
    glow = Image.new("RGB", (W, H), BG)
    g = ImageDraw.Draw(glow)
    g.ellipse((-220, -260, 520, 360), fill="#b9cffb")      # blue, top left
    g.ellipse((760, -300, 1500, 300), fill="#ddd0fb")      # violet, top right
    g.ellipse((780, 380, 1460, 960), fill="#c3ecea")       # teal, bottom right
    g.ellipse((-260, 420, 380, 980), fill="#f8dccd")       # peach, bottom left
    img = glow.filter(ImageFilter.GaussianBlur(110))
    d = ImageDraw.Draw(img)

    d.text((60, 70), "Plagiarism", font=font(34, "bold"), fill="#4f5ff0")

    d.text((60, 150), "Plagiarism won't go", font=font(56, "black"), fill=INK)
    gradient_text(img, (60, 216), "unnoticed", font(56, "black"),
                  [(0, (224, 71, 61)), (0.6, (214, 58, 122)), (1, (139, 79, 232))])
    y = 356
    for line in ("Add a thesis PDF, press Start, get an", "interactive report you can export."):
        d.text((60, y), line, font=font(27), fill=MUTED)
        y += 40

    pill = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pill)
    label = "Windows · macOS · Linux  ·  English · Lietuvių"
    lf = font(21, "bold")
    lw = d.textlength(label, font=lf)
    pd.rounded_rectangle((60, 506, 60 + lw + 44, 556), 25, fill=(255, 255, 255, 170), outline=(255, 255, 255, 255), width=2)
    img.paste(pill, (0, 0), pill)
    d.text((82, 518), label, font=lf, fill="#4f5ff0")

    # screenshot in a glass frame
    shot = Image.open(ASSETS / "screens" / "en" / "progress.png").convert("RGB").resize((660, 440), Image.LANCZOS)
    x0, y0, pad = 690, 96, 10
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((x0 + 16, y0 + 30, x0 + 660 + 2 * pad, y0 + 440 + 2 * pad + 18), 28, fill=(30, 40, 90, 70))
    sh = sh.filter(ImageFilter.GaussianBlur(26))
    img.paste(sh, (0, 0), sh)
    glass = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glass).rounded_rectangle((x0, y0, x0 + 660 + 2 * pad, y0 + 440 + 2 * pad), 28,
                                            fill=(255, 255, 255, 150), outline=(255, 255, 255, 235), width=2)
    img.paste(glass, (0, 0), glass)
    mask = Image.new("L", shot.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, shot.width - 1, shot.height - 1), 20, fill=255)
    img.paste(shot, (x0 + pad, y0 + pad), mask)

    out = ASSETS / "og-image.png"
    img.save(out, optimize=True)
    print(out, out.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
