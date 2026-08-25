"""
generate_icon.py — creates icon.ico and static/favicon.ico from scratch using Pillow.
Run once: python generate_icon.py
"""
from PIL import Image, ImageDraw
import math, os

BG      = (30, 58, 95)       # #1e3a5f
BARS    = [(96, 165, 250),   # #60a5fa  short
           (96, 165, 250),   # #60a5fa  medium
           (147, 197, 253),  # #93c5fd  tall
           (191, 219, 254)]  # #bfdbfe  tallest


def draw_icon(size: int) -> Image.Image:
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    radius = max(2, round(size * 0.2))
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BG)

    pad   = round(size * 0.15)
    gap   = max(1, round(size * 0.04))
    n     = 4
    total_gap = gap * (n - 1)
    bar_w = max(2, round((size - 2 * pad - total_gap) / n))

    # Bar heights as fractions of usable vertical space
    fracs  = [0.28, 0.50, 0.72, 0.88]
    max_h  = size - 2 * pad
    bar_rx = max(1, round(bar_w * 0.25))

    for i, frac in enumerate(fracs):
        bh   = round(max_h * frac)
        x0   = pad + i * (bar_w + gap)
        y0   = size - pad - bh
        x1   = x0 + bar_w
        y1   = size - pad
        draw.rounded_rectangle([x0, y0, x1, y1], radius=bar_rx, fill=BARS[i])

    return img


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [draw_icon(s) for s in sizes]

    # Multi-size .ico at project root (for PyInstaller + Inno Setup)
    frames[0].save(
        "icon.ico",
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[1:],
    )
    print("icon.ico written")

    # favicon.ico for the browser tab (16, 32, 48)
    fav_frames = [draw_icon(s) for s in [16, 32, 48]]
    os.makedirs("static", exist_ok=True)
    fav_frames[0].save(
        "static/favicon.ico",
        format="ICO",
        sizes=[(s, s) for s in [16, 32, 48]],
        append_images=fav_frames[1:],
    )
    print("static/favicon.ico written")


if __name__ == "__main__":
    main()
