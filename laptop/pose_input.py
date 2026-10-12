"""Camera paddle: your hand, seen by the webcam through MediaPipe Pose, moves the paddle.

Two parts:

  PoseProcessor  runs inside the shared camera thread (camera.Camera): each
                 frame -> MediaPipe -> latest hand reading. The game never
                 waits for it; it just reads the newest result.
  CameraPaddle   paddle source with the same interface as inputs.KeyboardPaddle
                 (update(dt), .x), plus calibration, smoothing, and drawing
                 the paddle position on the camera view.

Pose approach from the Pose race project (ME193-car/track_hands.py): the
PoseLandmarker task with pose_landmarker_lite.task, wrists = landmarks 15/16,
image mirrored so it feels like a mirror.

Holding the real paddle (with the UNO Q on it) often hides the wrist from the
camera, so the hand position comes from the best thing MediaPipe can see:
  "wrist"  the wrist itself (visibility >= MIN_WRIST_VISIBILITY)
  "hand"   else the index/pinky knuckles, plus the knuckles->wrist offset
           measured the last time the wrist was visible
  "elbow"  else the elbow, plus the last seen elbow->wrist offset (forearm)
  "hold"   nothing usable: the paddle stays put, for up to TRACK_HOLD_S
  "card"   the hand found is on or next to an AprilTag in view (within
           CARD_GUARD_MARGIN): that's the free hand holding a card (MediaPipe
           can mix up the two wrists), so it is ignored and the paddle stays
           put, as for "hold"
  "lost"   after that: paddle frozen, "no hand" shown
Using the offsets measured from the real wrist keeps the paddle from jumping
when the source changes.
"""
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import mediapipe as mp
import pygame
from mediapipe.tasks.python import vision

import config

REPO_ROOT = Path(__file__).resolve().parent.parent

# MediaPipe pose landmark numbers. "left"/"right" are YOUR left/right (the
# person's), not the image's.
WRIST_LANDMARK = {"left": 15, "right": 16}
KNUCKLE_LANDMARKS = {"left": (17, 19), "right": (18, 20)}   # pinky, index
ELBOW_LANDMARK = {"left": 13, "right": 14}
SKELETON = [(c.start, c.end) for c in vision.PoseLandmarksConnections.POSE_LANDMARKS]

# A reading older than this (camera stalled) counts as no reading.
STALE_S = 0.5

# Colors of each tracking source (camera view dot and on-screen badge)
SOURCE_COLORS = {"wrist": (90, 230, 120), "hand": (255, 200, 60), "elbow": (255, 150, 50),
                 "hold": (255, 150, 50), "card": (110, 170, 255), "lost": (220, 60, 70)}


@dataclass
class PoseReading:
    wrist_x: Optional[float]   # 0..1 across the mirrored image (0 = your left), None = no hand
    wrist_y: Optional[float]
    time: float                # time.monotonic() when the frame was processed
    source: Optional[str] = None   # "wrist" / "hand" / "elbow" (see the top of this file)


class PoseProcessor:
    """Camera processor: finds the tracked hand in each frame (see camera.py)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._latest: Optional[PoseReading] = None
        self._landmarker = None
        self._t0 = 0.0
        self._last_ts = -1
        # Offsets (wrist - knuckles, wrist - elbow) in image units, measured
        # whenever the wrist and that landmark are both visible
        self._knuckle_offset = None
        self._elbow_offset = None

    def latest(self) -> Optional[PoseReading]:
        with self._lock:
            return self._latest

    def setup(self):
        self._landmarker = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(
                model_asset_path=str(REPO_ROOT / config.POSE_MODEL_PATH)),
            running_mode=vision.RunningMode.VIDEO,   # tracks between frames: faster, steadier
            num_poses=1,
        ))
        self._t0 = time.monotonic()

    def process(self, rgb, preview, now):
        # Detect on the ORIGINAL (unmirrored) image. If MediaPipe saw a
        # mirrored image it would think your right arm is your left one.
        # Mirroring is applied afterwards: to x coordinates and the preview.
        ts = max(int((now - self._t0) * 1000), self._last_ts + 1)  # must increase
        self._last_ts = ts
        result = self._landmarker.detect_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)

        h, w, _ = preview.shape
        hand = None
        if result.pose_landmarks:
            lm = result.pose_landmarks[0]
            px = lambda x, y: (int((1 - x) * w), int(y * h))   # mirrored pixel
            for a, b in SKELETON:
                cv2.line(preview, px(lm[a].x, lm[a].y), px(lm[b].x, lm[b].y), (0, 200, 255), 2)
            hand = self.estimate_hand(lm)
            if hand is not None:
                color = SOURCE_COLORS[hand[2]]
                cv2.circle(preview, px(hand[0], hand[1]), 14, color, -1)
        with self._lock:
            if hand is None:
                self._latest = PoseReading(None, None, now)
            else:
                self._latest = PoseReading(1 - hand[0], hand[1], now, hand[2])

    def estimate_hand(self, lm) -> Optional[tuple]:
        """(x, y, source) of the wrist in the UNMIRRORED image, or None."""
        side = config.TRACK_WRIST
        wrist = lm[WRIST_LANDMARK[side]]
        knuckles = [lm[i] for i in KNUCKLE_LANDMARKS[side] if _seen(lm[i], config.FALLBACK_MIN_VISIBILITY)]
        elbow = lm[ELBOW_LANDMARK[side]]
        elbow_ok = _seen(elbow, config.FALLBACK_MIN_VISIBILITY)
        knuckle_xy = (None if not knuckles else
                      (sum(k.x for k in knuckles) / len(knuckles), sum(k.y for k in knuckles) / len(knuckles)))

        if _seen(wrist, config.MIN_WRIST_VISIBILITY):
            # Wrist visible: use it, and remember where it is relative to the
            # knuckles and the elbow for when it gets hidden.
            if knuckle_xy is not None:
                self._knuckle_offset = (wrist.x - knuckle_xy[0], wrist.y - knuckle_xy[1])
            if elbow_ok:
                self._elbow_offset = (wrist.x - elbow.x, wrist.y - elbow.y)
            return wrist.x, wrist.y, "wrist"
        if knuckle_xy is not None:
            ox, oy = self._knuckle_offset or (0.0, 0.0)
            return _clamp01(knuckle_xy[0] + ox), _clamp01(knuckle_xy[1] + oy), "hand"
        if elbow_ok and self._elbow_offset is not None:
            ox, oy = self._elbow_offset
            return _clamp01(elbow.x + ox), _clamp01(elbow.y + oy), "elbow"
        return None

    def close(self):
        if self._landmarker is not None:
            self._landmarker.close()


def _seen(p, min_visibility: float) -> bool:
    """MediaPipe is confident enough that this landmark is visible, and it's in the frame."""
    visibility = p.visibility if p.visibility is not None else 1.0
    return visibility >= min_visibility and 0 <= p.x <= 1 and 0 <= p.y <= 1


def _clamp01(v: float) -> float:
    return min(max(v, 0.0), 1.0)


# Calibration steps, in order
CALIB_LEFT = "left"
CALIB_RIGHT = "right"
CALIB_HIGH = "high"
CALIB_LOW = "low"
READY = "ready"
CALIB_STEPS = [CALIB_LEFT, CALIB_RIGHT, CALIB_HIGH, CALIB_LOW, READY]
CALIB_PROMPTS = {CALIB_LEFT: "to your LEFT edge", CALIB_RIGHT: "to your RIGHT edge",
                 CALIB_HIGH: "HIGH (highest ball you'd hit)", CALIB_LOW: "LOW (lowest ball you'd hit)"}


class CameraPaddle:
    """Paddle source driven by the hand. Same interface as inputs.KeyboardPaddle.

    Calibration: at startup it asks you to reach to your left edge, your
    right edge, then high, then low. Each is a countdown; the hand position is
    averaged over the last CALIB_SAMPLE_S seconds of it. Those numbers become
    the ends of the paddle's range: hand at your left edge -> paddle far left,
    right edge -> far right, high -> paddle at PADDLE_Y_MAX_M, low -> at
    PADDLE_Y_MIN_M, linear in between.
    """

    kind = "camera"

    def __init__(self, pose: PoseProcessor, camera=None,
                 start_x: float = 0.0, start_y: float = 0.3, tags=None):
        self.pose = pose
        self.tags = tags       # apriltag_input.TagProcessor, for the card guard (or None)
        self.camera = camera   # only used to report camera errors
        self.x = start_x   # paddle position in the hitting plane (m)
        self.y = start_y
        self.has_hand = False      # hand tracked (any source, or holding): False only when lost
        self.tracking = "lost"     # wrist / hand / elbow / hold / lost
        self.left_x: Optional[float] = None    # calibrated hand x at your left edge (0..1)
        self.right_x: Optional[float] = None   # ... and right edge
        self.high_y: Optional[float] = None    # calibrated hand y high (0 = top of image)
        self.low_y: Optional[float] = None     # ... and low
        self._reading: Optional[PoseReading] = None
        self._last_seen: Optional[float] = None   # time of the last usable reading
        self.start_calibration()

    # ------------------------------------------------------------ interface
    @property
    def ready(self) -> bool:
        """True once calibrated (the game won't start before that)."""
        return self.step == READY

    @property
    def calib_info(self) -> Optional[tuple]:
        """For the calibration screen: (step, step number 1-4, seconds left,
        short note or "") while calibrating, else None. step is "left",
        "right", "high" or "low"; the note is one or two words ("NO HAND",
        "REACH FARTHER", "TRY AGAIN"). The full sentence is in message."""
        if self.step == READY:
            return None
        left = max(0.0, config.CALIB_COUNTDOWN_S - (time.monotonic() - self._step_start))
        note = self._calib_note
        short = ("" if not note else "NO HAND" if note.startswith("no hand")
                 else "REACH FARTHER" if note.startswith("range only") else "TRY AGAIN")
        return self.step, CALIB_STEPS.index(self.step) + 1, left, short

    @property
    def message(self) -> Optional[str]:
        """Full text (camera error, or the calibration step in a sentence);
        the normal screen uses calib_info, the debug overlay this."""
        error = getattr(self.camera, "error", None)
        if error:
            return error
        if self.step == READY:
            return None
        hand = config.TRACK_WRIST.upper()
        left = max(0.0, config.CALIB_COUNTDOWN_S - (time.monotonic() - self._step_start))
        n = CALIB_STEPS.index(self.step) + 1
        extra = f"\n{self._calib_note}" if self._calib_note else ""
        return (f"Calibrate {n}/4: reach your {hand} hand {CALIB_PROMPTS[self.step]}... "
                f"{math.ceil(left)}{extra}")

    def handle_event(self, event):
        """C recalibrates (game.py only passes this on the waiting screen)."""
        if event.type == pygame.KEYDOWN and event.key == pygame.K_c:
            self.start_calibration()

    def start_calibration(self):
        self.step = CALIB_LEFT
        self._step_start = time.monotonic()
        self._samples = []
        self._calib_note = ""

    def update(self, dt: float):
        now = time.monotonic()
        r = self.pose.latest()
        self._reading = r
        fresh = r is not None and r.wrist_x is not None and now - r.time < STALE_S
        near_card = fresh and self._near_tag(r.wrist_x, r.wrist_y, now)
        if near_card:
            fresh = False             # the hand holding a card: don't follow it
        if fresh:
            self._last_seen = now
            self.tracking = r.source or "wrist"
        elif self._last_seen is not None and now - self._last_seen <= config.TRACK_HOLD_S:
            self.tracking = "card" if near_card else "hold"   # keep the paddle where it is
        else:
            self.tracking = "lost"
        self.has_hand = self.tracking != "lost"

        if self.step != READY:
            self._calibrate(now, fresh)
            return
        if not fresh:
            return   # hold / lost: the paddle stays where it is

        # Map the hand between the calibrated edges (0..1) across the paddle's range.
        t = (r.wrist_x - self.left_x) / (self.right_x - self.left_x)
        t = min(max(t, 0.0), 1.0)
        target_x = -config.PADDLE_X_RANGE_M + t * 2 * config.PADDLE_X_RANGE_M
        # Hand between the calibrated high and low spots -> paddle height.
        u = (r.wrist_y - self.high_y) / (self.low_y - self.high_y)
        u = min(max(u, 0.0), 1.0)
        target_y = config.PADDLE_Y_MAX_M + u * (config.PADDLE_Y_MIN_M - config.PADDLE_Y_MAX_M)

        # Exponential moving average: move a fraction of the way to the target
        # each frame, which smooths out MediaPipe's frame-to-frame jitter.
        self.x += config.SMOOTHING_ALPHA * (target_x - self.x)
        self.y += config.SMOOTHING_ALPHA * (target_y - self.y)

    def _near_tag(self, x: float, y: float, now: float) -> bool:
        """Is (x, y) (mirrored image fractions) on or within CARD_GUARD_MARGIN
        of an AprilTag seen in the latest camera frame?"""
        reading = self.tags.latest() if self.tags is not None else None
        if reading is None or not reading.boxes or now - reading.time > STALE_S:
            return False
        m = config.CARD_GUARD_MARGIN
        return any(x0 - m <= x <= x1 + m and y0 - m <= y <= y1 + m
                   for x0, y0, x1, y1 in reading.boxes.values())

    def draw_on_preview(self, surf: pygame.Surface, debug: bool = False):
        """On the camera view: the calibrated range (shaded outside, green edges
        labeled L / R / HI / LO), and the tracked hand (ring colored by its
        source). During calibration: the edges recorded so far. With debug:
        also the span in % and the tracking source next to the hand."""
        w, h = surf.get_size()
        font = pygame.font.SysFont(None, 22)
        edge_color = (90, 230, 120)
        done = CALIB_STEPS.index(self.step)   # number of finished steps
        vertical = [(v, lbl) for v, lbl, i in ((self.left_x, "L", 0), (self.right_x, "R", 1)) if done > i]
        horizontal = [(v, lbl) for v, lbl, i in ((self.high_y, "HI", 2), (self.low_y, "LO", 3)) if done > i]
        if self.step == READY:
            # Darken outside the calibrated box
            shade = pygame.Surface((w, h), pygame.SRCALPHA)
            shade.fill((0, 0, 0, 120))
            shade.fill((0, 0, 0, 0), pygame.Rect(self.left_x * w, self.high_y * h,
                                                 (self.right_x - self.left_x) * w,
                                                 (self.low_y - self.high_y) * h))
            surf.blit(shade, (0, 0))
            span = font.render(f"range {(self.right_x - self.left_x) * 100:.0f}% x "
                               f"{(self.low_y - self.high_y) * 100:.0f}% of view", True, edge_color)
            if debug:
                surf.blit(span, ((self.left_x + self.right_x) / 2 * w - span.get_width() / 2, h - 22))
        for x, label in vertical:
            pygame.draw.line(surf, edge_color, (x * w, 0), (x * w, h), 3)
            tag = font.render(label, True, (0, 0, 0), edge_color)
            surf.blit(tag, (x * w - tag.get_width() / 2, 4))
        for y, label in horizontal:
            pygame.draw.line(surf, edge_color, (0, y * h), (w, y * h), 3)
            tag = font.render(label, True, (0, 0, 0), edge_color)
            surf.blit(tag, (4, y * h - tag.get_height() / 2))
        r = self._reading
        if r is not None and r.wrist_x is not None and self.tracking in ("wrist", "hand", "elbow"):
            color = SOURCE_COLORS[self.tracking]
            wx, wy = r.wrist_x * w, r.wrist_y * h
            pygame.draw.line(surf, color, (wx, 0), (wx, h), 1)
            pygame.draw.circle(surf, color, (wx, wy), 11, 3)
            if debug and self.tracking != "wrist":
                tag = font.render(self.tracking, True, (0, 0, 0), color)
                surf.blit(tag, (wx + 14, wy - 8))
        else:
            msg = font.render({"hold": "hold", "card": "hand at card: ignored"}.get(self.tracking, "no hand"), True,
                              (255, 255, 255), (200, 50, 60))
            surf.blit(msg, (w / 2 - msg.get_width() / 2, h / 2 - 10))

    def stop(self):
        pass   # the camera belongs to game.py, which stops it

    # ------------------------------------------------------------ calibration
    def _calibrate(self, now: float, fresh: bool):
        elapsed = now - self._step_start
        # Collect the hand position during the last CALIB_SAMPLE_S of the countdown.
        if elapsed >= config.CALIB_COUNTDOWN_S - config.CALIB_SAMPLE_S and fresh:
            r = self._reading
            self._samples.append(r.wrist_x if self.step in (CALIB_LEFT, CALIB_RIGHT) else r.wrist_y)
        if elapsed < config.CALIB_COUNTDOWN_S:
            return

        if not self._samples:
            self._restart_step("no hand seen, try again")
            return
        value = sum(self._samples) / len(self._samples)
        if self.step == CALIB_LEFT:
            self.left_x = value
        elif self.step == CALIB_RIGHT:
            # The range must cover enough of the camera image: a narrow range
            # makes tiny hand moves sweep the paddle across the whole table.
            # (In the mirrored image, your left is the image's left.)
            if not self._span_ok(value - self.left_x, config.CALIB_MIN_SPAN, "width",
                                 "right edge wasn't right of the left one", "stand centered, reach farther",
                                 f"left={self.left_x:.2f} right={value:.2f}"):
                note = self._calib_note
                self.start_calibration()     # redo left and right
                self._calib_note = note
                return
            self.right_x = value
        elif self.step == CALIB_HIGH:
            self.high_y = value
        elif self.step == CALIB_LOW:
            # Image y grows downward, so low must be below (bigger than) high.
            if not self._span_ok(value - self.high_y, config.CALIB_MIN_SPAN_Y, "height",
                                 "low wasn't below high", "reach higher and lower",
                                 f"high={self.high_y:.2f} low={value:.2f}"):
                self.step = CALIB_HIGH       # redo just the height part
                self._restart_step(self._calib_note)
                return
            self.low_y = value
        self.step = CALIB_STEPS[CALIB_STEPS.index(self.step) + 1]
        self._restart_step("")
        if self.step == READY:
            print(f"Calibrated: left={self.left_x:.2f} right={self.right_x:.2f} (of frame width), "
                  f"high={self.high_y:.2f} low={self.low_y:.2f} (of frame height)")

    def _span_ok(self, span: float, minimum: float, dimension: str, backwards: str,
                 hint: str, values: str) -> bool:
        """Accept a calibrated range, or set the note explaining why not."""
        if span >= minimum:
            return True
        if span <= 0:
            self._calib_note = f"{backwards}: try again"
        else:
            self._calib_note = (f"range only {span * 100:.0f}% of the camera {dimension} "
                                f"(need {minimum * 100:.0f}%): {hint}")
        print(f"Calibration rejected: {values} ({span * 100:.0f}% of the frame {dimension}, "
              f"need {minimum * 100:.0f}%)")
        return False

    def _restart_step(self, note: str):
        self._step_start = time.monotonic()
        self._samples = []
        self._calib_note = note
