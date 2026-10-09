"""AprilTags: hold up a printed tag to pick the level and start, or to reset the record.

Two parts:

  TagProcessor  runs inside the shared camera thread (camera.Camera): finds
                tags in each frame and draws their outline, ID, and meaning on
                the camera view.
  TagStart      control source with the same poll() interface as
                inputs.KeyboardStart. It turns "tag held up for TAG_HOLD_S
                seconds" into actions for game.py.

Detection approach from the AprilTag parking project
(ME193-3-AI-in-Mobile-Robots/AprilTag Parking/AprilTagParking.py): OpenCV's
aruco detector with the AprilTag 36h11 dictionary.
"""
import threading
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
import pygame

import config

DICTIONARIES = {"tag36h11": cv2.aruco.DICT_APRILTAG_36h11}

OUTLINE = (255, 60, 60)
LABEL_BG = (0, 0, 0)
LABEL_FG = (255, 255, 255)
BAR_BG = (40, 40, 40)
BAR_FG = (120, 220, 140)
BAR_RESET = (255, 110, 110)


def tag_meaning(tag_id: int) -> str:
    """What a tag does, for labels: a level name, "reset best", or "unused"."""
    if tag_id in config.TAG_LEVELS:
        level = config.TAG_LEVELS[tag_id]
        return f"Level {level}: {config.LEVELS[level]['name']}"
    if tag_id == config.TAG_RESET_BEST:
        return "Reset best"
    return "unused"


@dataclass
class TagReading:
    ids: list      # tag IDs seen in this frame
    time: float    # time.monotonic() when the frame was processed


class TagProcessor:
    """Camera processor: detects AprilTags in each frame (see camera.py)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._latest: Optional[TagReading] = None
        self._detector = None

    def latest(self) -> Optional[TagReading]:
        with self._lock:
            return self._latest

    def setup(self):
        dictionary = cv2.aruco.getPredefinedDictionary(DICTIONARIES[config.TAG_FAMILY])
        self._detector = cv2.aruco.ArucoDetector(dictionary)

    def process(self, rgb, preview, now):
        # Detect on the original frame (a mirrored tag reads as a different,
        # invalid pattern), then mirror the corners for drawing.
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        corners, ids, _ = self._detector.detectMarkers(gray)
        seen = []
        if ids is not None:
            w = preview.shape[1]
            for tag_corners, tag_id in zip(corners, ids.ravel()):
                tag_id = int(tag_id)
                seen.append(tag_id)
                pts = tag_corners[0].copy()
                pts[:, 0] = (w - 1) - pts[:, 0]   # mirror x
                cv2.polylines(preview, [pts.astype(np.int32)], True, OUTLINE, 4)
                x, y = pts[:, 0].min(), pts[:, 1].min()
                self._label(preview, f"ID {tag_id}  {tag_meaning(tag_id)}", int(x), int(y) - 10)
        with self._lock:
            self._latest = TagReading(seen, now)

    def close(self):
        pass

    @staticmethod
    def _label(img, text, x, y):
        font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2
        (tw, th), _ = cv2.getTextSize(text, font, scale, thick)
        x = max(0, min(x, img.shape[1] - tw - 4))
        y = max(th + 4, y)
        cv2.rectangle(img, (x, y - th - 6), (x + tw + 6, y + 4), LABEL_BG, -1)
        cv2.putText(img, text, (x + 3, y), font, scale, LABEL_FG, thick, cv2.LINE_AA)


class TagStart:
    """Turns held-up tags into actions, like inputs.KeyboardStart.

    poll() returns a list of (action, value):
      ("level", n)   a level tag just came into view: show that level
      ("start", n)   a level tag was held for TAG_HOLD_S: start at level n
      ("reset", None) the reset tag was held for TAG_HOLD_S: reset best_streak

    "Held" means the same tag stayed in view, allowing gaps shorter than
    TAG_DROPOUT_S (detection flickers). Each hold acts once; take the tag away
    and show it again to act again.
    """

    def __init__(self, tags: TagProcessor):
        self.tags = tags
        self.current: Optional[int] = None   # tag being held, or None
        self._since = 0.0                    # when the current hold began
        self._last_seen = 0.0
        self._fired = False                  # current hold already acted
        self._actions = []

    @property
    def progress(self) -> float:
        """0..1: how far the current hold is toward acting."""
        if self.current is None or self._fired:
            return 1.0 if self._fired else 0.0
        return min(1.0, (time.monotonic() - self._since) / config.TAG_HOLD_S)

    def update(self, can_start: bool = True):
        """can_start: False while the game can't start (playing, or the camera
        paddle isn't calibrated). A level tag's hold doesn't count down then,
        so the bar doesn't fill for nothing; it starts counting once allowed."""
        now = time.monotonic()
        reading = self.tags.latest()
        useful = [t for t in (reading.ids if reading else [])
                  if t in config.TAG_LEVELS or t == config.TAG_RESET_BEST]
        fresh = reading is not None and now - reading.time < config.TAG_DROPOUT_S

        if fresh and useful and (self.current in useful):
            self._last_seen = now                      # same tag still held
        elif fresh and useful:
            self._begin(useful[0], now)                # a new tag appeared
        elif self.current is not None and now - self._last_seen > config.TAG_DROPOUT_S:
            self.current = None                        # tag gone: hold ends

        if self.current in config.TAG_LEVELS and not can_start and not self._fired:
            self._since = now                          # hold the countdown at 0

        if (self.current is not None and not self._fired
                and now - self._since >= config.TAG_HOLD_S):
            self._fired = True
            if self.current == config.TAG_RESET_BEST:
                self._actions.append(("reset", None))
            else:
                self._actions.append(("start", config.TAG_LEVELS[self.current]))

    def poll(self) -> list:
        actions, self._actions = self._actions, []
        return actions

    def draw_on_preview(self, surf: pygame.Surface):
        """Progress bar along the bottom of the camera view while a tag is held."""
        if self.current is None:
            return
        w, h = surf.get_size()
        bar = pygame.Rect(8, h - 22, w - 16, 14)
        pygame.draw.rect(surf, BAR_BG, bar)
        fill = bar.copy()
        fill.width = int(bar.width * self.progress)
        color = BAR_RESET if self.current == config.TAG_RESET_BEST else BAR_FG
        pygame.draw.rect(surf, color, fill)
        pygame.draw.rect(surf, (255, 255, 255), bar, 1)

    def _begin(self, tag_id: int, now: float):
        self.current = tag_id
        self._since = self._last_seen = now
        self._fired = False
        if tag_id in config.TAG_LEVELS:
            self._actions.append(("level", config.TAG_LEVELS[tag_id]))
