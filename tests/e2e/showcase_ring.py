"""README picture of the in-game ring, drawn by the skill's own RingRenderer.

The ring normally needs Windows for its window; drawing a frame does not, so
the Win32 module is replaced by an empty stand-in for this render only.
"""
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SIZE = 220  # the skill's default overlay diameter
ZOOM = 2
STATES = (
    ("Bear right 15°", (14.5, -3.8), "1.0 km"),
    ("Turn right", (62.0, 4.0), "2.4 km"),
    ("Target set, no position fix yet", None, ""),
)


def renderer():
    import sc_voice_cockpit.navpoint as package
    if "sc_voice_cockpit.navpoint.ring_overlay" not in sys.modules:
        stub = types.ModuleType("sc_voice_cockpit.navpoint.overlay_win32")
        sys.modules.setdefault(stub.__name__, stub)
        package.overlay_win32 = sys.modules[stub.__name__]
    from sc_voice_cockpit.navpoint.ring_overlay import RingRenderer
    return RingRenderer(SIZE)


def ring_image(ring, deflection, distance):
    import numpy as np
    from PIL import Image
    pixels = np.frombuffer(ring.frame(deflection, 0.0, "", 1.0, 0.0, distance=distance), dtype=np.uint8)
    pixels = pixels.reshape(SIZE, SIZE, 4)[..., [2, 1, 0, 3]].astype(np.float32)
    alpha = pixels[..., 3:4] / 255.0
    # Premultiplied BGRA from the renderer; PIL composites straight RGBA.
    pixels[..., :3] = np.where(alpha > 0, pixels[..., :3] / np.maximum(alpha, 1e-6), 0)
    image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGBA")
    return image.resize((SIZE * ZOOM, SIZE * ZOOM), Image.LANCZOS)


def render(path):
    import random
    from PIL import Image, ImageDraw, ImageFont
    ring = renderer()
    side, gap, top, label = SIZE * ZOOM, 56, 40, 84
    width = len(STATES) * side + (len(STATES) + 1) * gap
    height = top + side + label
    # A dark sky, as the ring hangs over the game at the top of the screen.
    scene = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(scene)
    for y in range(height):
        t = y / max(1, height - 1)
        draw.line([(0, y), (width, y)], fill=(int(6 + 10 * t), int(10 + 16 * t), int(18 + 24 * t), 255))
    stars = random.Random(7)
    for _ in range(220):
        x, y = stars.randrange(width), stars.randrange(height)
        level = stars.randrange(70, 190)
        draw.point((x, y), fill=(level, level, min(255, level + 25), 255))
    try:
        font = ImageFont.load_default(size=26)
    except TypeError:
        font = ImageFont.load_default()
    for index, (caption, deflection, distance) in enumerate(STATES):
        x = gap + index * (side + gap)
        scene.alpha_composite(ring_image(ring, deflection, distance), (x, top))
        box = draw.textbbox((0, 0), caption, font=font)
        draw.text((x + (side - (box[2] - box[0])) / 2, top + side + 26), caption, font=font, fill=(200, 214, 232, 255))
    scene.convert("RGB").save(path, optimize=True)


if __name__ == "__main__":
    render(Path(sys.argv[1]))
