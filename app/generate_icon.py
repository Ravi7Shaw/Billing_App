"""
generate_icon.py
-----------------
Generates the application icon: a rounded-square badge in the app's brand
green, with a white receipt (billing) and a gold rupee coin (retail/currency)
-- drawn at high resolution and downsampled for crisp edges at every size,
then packed into a multi-resolution .ico for Windows plus standalone PNGs.

Run once with: python generate_icon.py
Output: assets/icon.ico, assets/icon_256.png, assets/icon_512.png
"""

import os
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = os.path.join(os.path.dirname(__file__), "assets")
os.makedirs(OUT_DIR, exist_ok=True)

# Supersample at 4x the largest target size for smooth anti-aliased edges,
# then downscale with a high-quality filter.
CANVAS = 1024
SS = 4
S = CANVAS * SS

# Brand colors (matches theme.py)
BG_TOP = (58, 122, 88)        # lighter green
BG_BOTTOM = (26, 63, 44)      # primary_dark
WHITE = (255, 255, 255)
PAPER_LINE = (150, 165, 155)
PAPER_LINE_LIGHT = (195, 205, 198)
GOLD = (199, 123, 48)
GOLD_DARK = (163, 96, 34)
SHADOW = (20, 45, 32, 80)


def vertical_gradient(size, top, bottom):
    img = Image.new("RGB", (1, size), color=0)
    for y in range(size):
        t = y / (size - 1)
        r = round(top[0] + (bottom[0] - top[0]) * t)
        g = round(top[1] + (bottom[1] - top[1]) * t)
        b = round(top[2] + (bottom[2] - top[2]) * t)
        img.putpixel((0, y), (r, g, b))
    return img.resize((size, size))


def rounded_mask(size, radius):
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def build_icon():
    # ---- Background: rounded square with a soft diagonal-feeling gradient
    bg_gradient = vertical_gradient(S, BG_TOP, BG_BOTTOM)
    mask = rounded_mask(S, radius=int(S * 0.22))
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    base.paste(bg_gradient, (0, 0), mask)

    draw = ImageDraw.Draw(base, "RGBA")

    # subtle inner highlight near the top for depth
    highlight = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    hd = ImageDraw.Draw(highlight)
    hd.ellipse(
        [S * 0.05, -S * 0.25, S * 0.95, S * 0.55],
        fill=(255, 255, 255, 22),
    )
    highlight.putalpha(Image.composite(highlight, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask).getchannel("A"))
    base.alpha_composite(highlight)

    # ---- Receipt card (white, rounded top, zigzag torn bottom edge)
    card_w = S * 0.48
    card_h = S * 0.60
    card_x0 = S * 0.24
    card_y0 = S * 0.185

    # drop shadow behind the card
    shadow_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow_layer)
    sd.rounded_rectangle(
        [card_x0 + S * 0.018, card_y0 + S * 0.028, card_x0 + card_w + S * 0.018, card_y0 + card_h * 0.86 + S * 0.028],
        radius=int(S * 0.03),
        fill=(15, 35, 24, 90),
    )
    shadow_layer = shadow_layer.filter(__import__("PIL.ImageFilter", fromlist=["GaussianBlur"]).GaussianBlur(S * 0.01))
    base.alpha_composite(shadow_layer)

    # card body with rounded top corners
    card = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    corner_r = S * 0.03
    zigzag_h = card_h * 0.14
    body_bottom = card_y0 + card_h - zigzag_h

    cd.rounded_rectangle(
        [card_x0, card_y0, card_x0 + card_w, body_bottom],
        radius=int(corner_r),
        fill=WHITE,
    )
    # fill the lower square portion so the rounded_rectangle's bottom
    # corners don't show above the zigzag
    cd.rectangle([card_x0, card_y0 + corner_r, card_x0 + card_w, body_bottom], fill=WHITE)

    # zigzag (torn receipt) edge along the bottom
    teeth = 7
    tooth_w = card_w / teeth
    points = [(card_x0, body_bottom)]
    for i in range(teeth):
        x_mid = card_x0 + tooth_w * (i + 0.5)
        x_end = card_x0 + tooth_w * (i + 1)
        y_peak = body_bottom + zigzag_h
        y_base = body_bottom
        points.append((x_mid, y_peak))
        points.append((x_end, y_base))
    points.append((card_x0 + card_w, card_y0 + card_h))
    points.append((card_x0, card_y0 + card_h))
    cd.polygon(points, fill=WHITE)

    base.alpha_composite(card)

    # ---- Text lines on the receipt (itemized rows)
    line_x0 = card_x0 + card_w * 0.16
    line_x1_full = card_x0 + card_w * 0.84
    line_y_start = card_y0 + card_h * 0.20
    line_gap = card_h * 0.115
    line_thickness = max(2, int(S * 0.012))

    for i in range(4):
        y = line_y_start + line_gap * i
        x1 = line_x1_full if i != 3 else card_x0 + card_w * 0.55  # last line ("total") shorter
        color = PAPER_LINE if i != 3 else PAPER_LINE
        draw.rounded_rectangle(
            [line_x0, y, x1, y + line_thickness], radius=line_thickness // 2, fill=color
        )

    # a slightly bolder short line to suggest a header, above the items
    header_y = card_y0 + card_h * 0.10
    draw.rounded_rectangle(
        [line_x0, header_y, card_x0 + card_w * 0.62, header_y + line_thickness * 1.6],
        radius=line_thickness,
        fill=PAPER_LINE_LIGHT,
    )

    # ---- Gold coin badge (rupee) overlapping the bottom-right of the card
    coin_r = S * 0.175
    coin_cx = card_x0 + card_w * 0.92
    coin_cy = card_y0 + card_h * 0.90

    # coin shadow
    coin_shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    csd = ImageDraw.Draw(coin_shadow)
    csd.ellipse(
        [coin_cx - coin_r + S * 0.012, coin_cy - coin_r + S * 0.018,
         coin_cx + coin_r + S * 0.012, coin_cy + coin_r + S * 0.018],
        fill=(15, 35, 24, 110),
    )
    coin_shadow = coin_shadow.filter(__import__("PIL.ImageFilter", fromlist=["GaussianBlur"]).GaussianBlur(S * 0.008))
    base.alpha_composite(coin_shadow)

    draw.ellipse(
        [coin_cx - coin_r, coin_cy - coin_r, coin_cx + coin_r, coin_cy + coin_r],
        fill=GOLD, outline=GOLD_DARK, width=max(2, int(S * 0.008)),
    )
    # inner ring for a "coin" feel
    inner_r = coin_r * 0.8
    draw.ellipse(
        [coin_cx - inner_r, coin_cy - inner_r, coin_cx + inner_r, coin_cy + inner_r],
        outline=(255, 255, 255, 90), width=max(1, int(S * 0.004)),
    )

    font_path = os.path.join(
        __import__("matplotlib").get_data_path(), "fonts", "ttf", "DejaVuSans-Bold.ttf"
    )
    font = ImageFont.truetype(font_path, int(coin_r * 1.25))
    text = "\u20b9"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        (coin_cx - tw / 2 - bbox[0], coin_cy - th / 2 - bbox[1]),
        text, font=font, fill=WHITE,
    )

    return base


def save_all(img):
    # High quality downsample for the master sizes
    sizes_for_ico = [16, 24, 32, 48, 64, 128, 256]
    master = img.resize((CANVAS, CANVAS), Image.LANCZOS)

    master.save(os.path.join(OUT_DIR, "icon_1024.png"))
    master.resize((512, 512), Image.LANCZOS).save(os.path.join(OUT_DIR, "icon_512.png"))
    master.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT_DIR, "icon_256.png"))

    # Pillow's ICO writer generates each requested size itself by resampling
    # from the source image passed to save() -- it does NOT use separately
    # pre-resized frames the way GIF/TIFF multi-frame saves do.
    master.save(
        os.path.join(OUT_DIR, "icon.ico"),
        format="ICO",
        sizes=[(s, s) for s in sizes_for_ico],
    )
    print(f"Saved icon.ico and PNGs to {OUT_DIR}")


if __name__ == "__main__":
    icon = build_icon()
    save_all(icon)
