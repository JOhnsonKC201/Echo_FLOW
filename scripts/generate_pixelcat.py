"""Generate a cute pixel-art cat ("PixelCat") as a PNG.

Each character in GRID maps to a color. The small grid is rendered at a large
scale so the individual pixels stay crisp and blocky, giving that classic
pixel-art look.
"""

from PIL import Image

# Color palette
PALETTE = {
    ".": None,                 # transparent -> sky background shows through
    "B": (45, 42, 56),         # dark outline
    "O": (255, 150, 60),       # orange fur
    "o": (235, 125, 45),       # darker orange (shading)
    "L": (255, 200, 140),      # light belly / muzzle
    "P": (255, 170, 190),      # pink (inner ear, nose)
    "W": (250, 250, 252),      # white (eye highlight)
    "G": (90, 200, 120),       # green eyes
    "K": (20, 20, 24),         # pupils / whisker dots
}

# 24 x 24 PixelCat
GRID = [
    "........................",
    "....BB..........BB......",
    "...BooB........BooB.....",
    "...BoPoB......BoPoB.....",
    "...BoPPoBBBBBBoPPoB.....",
    "...BoPooOOOOOOooPoB.....",
    "...BoOOOOOOOOOOOOoB.....",
    "..BOOOOOOOOOOOOOOOOB....",
    "..BOOOOOOOOOOOOOOOOB....",
    ".BOOOGGWOOOOOOWGGOOOB...",
    ".BOOOGGKOOOOOOKGGOOOB...",
    ".BOOOGGGOOOOOOGGGOOOB...",
    ".BOOOOOOOOPPOOOOOOOOB...",
    ".BOOOOOOOOKKOOOOOOOOB...",
    ".BOOOOOLLLLLLLLOOOOOB...",
    ".BOOOOLLLLLLLLLLOOOOB...",
    "..BOOOLLLLLLLLLLOOOB....",
    "..BOOOOLLLLLLLLOOOOB....",
    "...BOOOOLLLLLLOOOOB.....",
    "....BOOOOOOOOOOOOB......",
    ".....BOOOOOOOOOOB.......",
    "......BOOoooooOOB.......",
    ".......BBoooooBB.......",
    ".........BBBBB.........",
]

SCALE = 24  # each grid cell becomes a SCALE x SCALE block

width = len(GRID[0]) * SCALE
height = len(GRID) * SCALE
img = Image.new("RGBA", (width, height), (135, 206, 235, 255))  # sky-blue bg

px = img.load()
for y, row in enumerate(GRID):
    for x, ch in enumerate(row):
        color = PALETTE.get(ch)
        if color is None:
            continue
        for dy in range(SCALE):
            for dx in range(SCALE):
                px[x * SCALE + dx, y * SCALE + dy] = color + (255,)

out = "assets/pixelcat.png"
img.save(out)
print(f"Saved {out} ({width}x{height})")
