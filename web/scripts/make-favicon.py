"""Render the Yapitalism mark to a multi-size favicon.ico.

Redrawn from src/app/icon.svg rather than rasterised through an SVG engine: the
mark is a rounded square plus a Y made only of straight segments, so the
geometry can be reproduced exactly and supersampled for clean edges at 16px,
where a generic SVG rasteriser tends to smear the stem.
"""

from PIL import Image, ImageDraw

VIEWBOX = 192
BG = (0x1B, 0x1D, 0x18, 0xFF)
MARK = (0xB9, 0xE6, 0x3B, 0xFF)
RADIUS = 40  # rx from the source SVG

# The Y, traced from the SVG path. Every command is a line, so the shape is a
# plain polygon:
#   M42 40 h28 l26 43 l26-43 h28 l-41 68 v44 H83 v-44 Z
Y_POLYGON = [
    (42, 40),
    (70, 40),
    (96, 83),
    (122, 40),
    (150, 40),
    (109, 108),
    (109, 152),
    (83, 152),
    (83, 108),
]

SIZES = [16, 32, 48, 64, 128, 256]
SUPERSAMPLE = 8


def render(size: int) -> Image.Image:
    big = size * SUPERSAMPLE
    scale = big / VIEWBOX
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        [(0, 0), (big - 1, big - 1)], radius=RADIUS * scale, fill=BG
    )
    draw.polygon([(x * scale, y * scale) for x, y in Y_POLYGON], fill=MARK)
    return image.resize((size, size), Image.LANCZOS)


frames = [render(s) for s in SIZES]
out = "/Users/aytuncyildizli/.superset/projects/relayproof/web/src/app/favicon.ico"
frames[-1].save(out, format="ICO", sizes=[(s, s) for s in SIZES])
print(f"wrote {out}")
