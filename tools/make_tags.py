"""Make printable AprilTags (tag36h11) for the game: one tag per US Letter page.

Run from the repo root:  python tools/make_tags.py
Writes assets/apriltags/tag36h11_id<N>.png for every tag the game uses
(level tags from TAG_LEVELS, the reset tag TAG_RESET_BEST, and the power-up
cards TAG_SLOWMO and TAG_EXTRA_LIFE in config.py).

Each tag sits in a white border (the quiet zone the detector needs, 1 tag
cell wide) marked by a thin grey cut line; the name is printed BELOW that
line, so cutting along it keeps the text off the border.

Same approach as the AprilTag parking project's AprilTagGenerator.py: OpenCV's
aruco module draws the marker, surrounded by a white quiet zone so the
detector can find the tag's edges. Pages are 300 dpi: print at 100% / "actual
size" (not "fit to page") and each tag comes out 6.4 inches wide.
"""
import sys
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "laptop"))
import config  # noqa: E402

OUT_DIR = REPO_ROOT / "assets" / "apriltags"
DPI = 300
PAGE_W, PAGE_H = int(8.5 * DPI), int(11 * DPI)   # US Letter
# A 36h11 marker is 8 x 8 cells (6 x 6 data + 1-cell black border); 240 px
# per cell keeps the cells sharp. 1920 px = 6.4 in at 300 dpi.
TAG_PX = 8 * 240
TAG_TOP = 450   # px from the top of the page to the tag
BORDER = TAG_PX // 8   # white border around the tag: one cell (240 px = 0.8 in)
FAMILIES = {"tag36h11": cv2.aruco.DICT_APRILTAG_36h11}


def meaning(tag_id: int) -> str:
    if tag_id in config.TAG_LEVELS:
        level = config.TAG_LEVELS[tag_id]
        return f"Level {level}: {config.LEVELS[level]['name']}"
    if tag_id == config.TAG_SLOWMO:
        return "SLOW-MO"
    if tag_id == config.TAG_EXTRA_LIFE:
        return "EXTRA LIFE"
    return "Reset best streak"


def subtitle(tag_id: int) -> str:
    if tag_id == config.TAG_SLOWMO:
        return (f"Power-up card, once per rally: {config.SLOWMO_SPEED:.0%} speed "
                f"for {config.SLOWMO_BALLS} balls (show it during a rally)")
    if tag_id == config.TAG_EXTRA_LIFE:
        return "Power-up card, once per rally: show it during the countdown after a miss"
    return "Show it on the start screen"


def make_page(dictionary, tag_id: int) -> Image.Image:
    marker = cv2.aruco.generateImageMarker(dictionary, tag_id, TAG_PX)
    page = Image.new("L", (PAGE_W, PAGE_H), 255)   # white page = quiet zone all around
    left = (PAGE_W - TAG_PX) // 2
    page.paste(Image.fromarray(marker), (left, TAG_TOP))

    draw = ImageDraw.Draw(page)
    # Cut line around the white border (nothing printed inside it but the tag)
    draw.rectangle([left - BORDER, TAG_TOP - BORDER, left + TAG_PX + BORDER, TAG_TOP + TAG_PX + BORDER],
                   outline=170, width=4)
    big = ImageFont.load_default(size=130)
    small = ImageFont.load_default(size=50)
    y = TAG_TOP + TAG_PX + BORDER + 70
    for text, font in [(meaning(tag_id), big),
                       (subtitle(tag_id), small),
                       (f"ID {tag_id}  -  AprilTag {config.TAG_FAMILY}  -  ME193 ping-pong", small),
                       ("Print at 100% (actual size). Keep the white border.", small)]:
        width = draw.textlength(text, font=font)
        draw.text(((PAGE_W - width) / 2, y), text, fill=0, font=font)
        y += font.size + 40
    return page


def main():
    dictionary = cv2.aruco.getPredefinedDictionary(FAMILIES[config.TAG_FAMILY])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag_ids = sorted(set(config.TAG_LEVELS) | {config.TAG_RESET_BEST, config.TAG_SLOWMO, config.TAG_EXTRA_LIFE})
    for tag_id in tag_ids:
        path = OUT_DIR / f"{config.TAG_FAMILY}_id{tag_id}.png"
        make_page(dictionary, tag_id).save(path, dpi=(DPI, DPI))
        print(f"Saved {path.relative_to(REPO_ROOT)}  ({meaning(tag_id)})")


if __name__ == "__main__":
    main()
