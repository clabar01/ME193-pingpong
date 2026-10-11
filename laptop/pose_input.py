"""Camera paddle: your wrist, seen by the webcam through MediaPipe Pose, moves the paddle.

Two parts:

  PoseProcessor  runs inside the shared camera thread (camera.Camera): each
                 frame -> MediaPipe -> latest wrist reading. The game never
                 waits for it; it just reads the newest result.
  CameraPaddle   paddle source with the same interface as inputs.KeyboardPaddle
                 (update(dt), .x), plus calibration, smoothing, and drawing
                 the paddle position on the camera view.

Pose approach from the Pose race project (ME193-car/track_hands.py): the
PoseLandmarker task with pose_landmarker_lite.task, wrists = landmarks 15/16,
image mirrored so it feels like a mirror.
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
SKELETON = [(c.start, c.end) for c in vision.PoseLandmarksConnections.POSE_LANDMARKS]

# A reading older than this (camera stalled) counts as "no hand".
STALE_S = 0.5


@dataclass
class PoseReading:
    wrist_x: Optional[float]   # 0..1 across the mirrored image (0 = your left), None = no wrist
    wrist_y: Optional[float]
    time: float                # time.monotonic() when the frame was processed


class PoseProcessor:
    """Camera processor: finds the tracked wrist in each frame (see camera.py)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._latest: Optional[PoseReading] = None
        self._landmarker = None
        self._t0 = 0.0
        self._last_ts = -1

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
        wrist_x = wrist_y = None
        if result.pose_landmarks:
            lm = result.pose_landmarks[0]
            px = lambda p: (int((1 - p.x) * w), int(p.y * h))   # mirrored pixel
            for a, b in SKELETON:
                cv2.line(preview, px(lm[a]), px(lm[b]), (0, 200, 255), 2)
            wrist = lm[WRIST_LANDMARK[config.TRACK_WRIST]]
            visibility = wrist.visibility if wrist.visibility is not None else 1.0
            if (visibility >= config.MIN_WRIST_VISIBILITY
                    and 0 <= wrist.x <= 1 and 0 <= wrist.y <= 1):
                wrist_x, wrist_y = 1 - wrist.x, wrist.y
                cv2.circle(preview, px(wrist), 14, (255, 80, 80), -1)
        with self._lock:
            self._latest = PoseReading(wrist_x, wrist_y, now)

    def close(self):
        if self._landmarker is not None:
            self._landmarker.close()


# Calibration steps
CALIB_LEFT = "left"
CALIB_RIGHT = "right"
READY = "ready"


class CameraPaddle:
    """Paddle source driven by the wrist. Same interface as inputs.KeyboardPaddle.

    Calibration: at startup it asks you to reach to your left edge, then your
    right edge. Each edge is a countdown; the wrist x is averaged over the last
    CALIB_SAMPLE_S seconds of it. Those two numbers become the ends of the
    paddle's range: wrist at your left edge -> paddle far left, right edge ->
    far right, linear in between. Up/down needs no calibration: the wrist's
    height in the image maps to the paddle's height (WRIST_Y_HIGH/LOW).
    """

    kind = "camera"

    def __init__(self, pose: PoseProcessor, camera=None,
                 start_x: float = 0.0, start_y: float = 0.3):
        self.pose = pose
        self.camera = camera   # only used to report camera errors
        self.x = start_x   # paddle position in the hitting plane (m)
        self.y = start_y
        self.has_hand = False
        self.left_x: Optional[float] = None    # calibrated wrist x at your left edge (0..1)
        self.right_x: Optional[float] = None   # ... and right edge
        self._reading: Optional[PoseReading] = None
        self.start_calibration()

    # ------------------------------------------------------------ interface
    @property
    def ready(self) -> bool:
        """True once calibrated (the game won't start before that)."""
        return self.step == READY

    @property
    def message(self) -> Optional[str]:
        """Big text for the screen, or None."""
        error = getattr(self.camera, "error", None)
        if error:
            return error
        if self.step == READY:
            return None
        hand = config.TRACK_WRIST.upper()
        edge = "LEFT" if self.step == CALIB_LEFT else "RIGHT"
        left = max(0.0, config.CALIB_COUNTDOWN_S - (time.monotonic() - self._step_start))
        extra = f"\n{self._calib_note}" if self._calib_note else ""
        return f"Calibrate: reach your {hand} hand to your {edge} edge... {math.ceil(left)}{extra}"

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
        self.has_hand = (r is not None and r.wrist_x is not None and now - r.time < STALE_S)

        if self.step != READY:
            self._calibrate(now)
            return
        if not self.has_hand:
            return   # no hand: freeze the paddle where it is

        # Map the wrist between the calibrated edges (0..1) across the paddle's range.
        t = (r.wrist_x - self.left_x) / (self.right_x - self.left_x)
        t = min(max(t, 0.0), 1.0)
        target_x = -config.PADDLE_X_RANGE_M + t * 2 * config.PADDLE_X_RANGE_M
        # Wrist height in the image -> paddle height (higher wrist = higher paddle).
        u = (r.wrist_y - config.WRIST_Y_HIGH) / (config.WRIST_Y_LOW - config.WRIST_Y_HIGH)
        u = min(max(u, 0.0), 1.0)
        target_y = config.PADDLE_Y_MAX_M + u * (config.PADDLE_Y_MIN_M - config.PADDLE_Y_MAX_M)

        # Exponential moving average: move a fraction of the way to the target
        # each frame, which smooths out MediaPipe's frame-to-frame jitter.
        self.x += config.SMOOTHING_ALPHA * (target_x - self.x)
        self.y += config.SMOOTHING_ALPHA * (target_y - self.y)

    def draw_on_preview(self, surf: pygame.Surface):
        """On the camera view: the calibrated range (shaded outside, green edges
        labeled L / R with the span in %), and the tracked wrist (yellow). During
        calibration: the wrist and the edge recorded so far."""
        w, h = surf.get_size()
        font = pygame.font.SysFont(None, 22)
        edge_color, wrist_color = (90, 230, 120), (255, 220, 60)
        edges = [(self.left_x, "L")] if self.left_x is not None and self.step != CALIB_LEFT else []
        if self.step == READY:
            edges.append((self.right_x, "R"))
            # Darken outside the calibrated range
            shade = pygame.Surface((w, h), pygame.SRCALPHA)
            shade.fill((0, 0, 0, 120))
            shade.fill((0, 0, 0, 0), pygame.Rect(self.left_x * w, 0, (self.right_x - self.left_x) * w, h))
            surf.blit(shade, (0, 0))
            span = font.render(f"range {(self.right_x - self.left_x) * 100:.0f}% of view", True, edge_color)
            surf.blit(span, ((self.left_x + self.right_x) / 2 * w - span.get_width() / 2, h - 22))
        for x, label in edges:
            pygame.draw.line(surf, edge_color, (x * w, 0), (x * w, h), 3)
            tag = font.render(label, True, (0, 0, 0), edge_color)
            surf.blit(tag, (x * w - tag.get_width() / 2, 4))
        r = self._reading
        if r is not None and r.wrist_x is not None and self.has_hand:
            wx, wy = r.wrist_x * w, r.wrist_y * h
            pygame.draw.line(surf, wrist_color, (wx, 0), (wx, h), 1)
            pygame.draw.circle(surf, wrist_color, (wx, wy), 11, 3)
        elif self.step != READY or not self.has_hand:
            msg = font.render("no wrist", True, (255, 255, 255), (200, 50, 60))
            surf.blit(msg, (w / 2 - msg.get_width() / 2, h / 2 - 10))

    def stop(self):
        pass   # the camera belongs to game.py, which stops it

    # ------------------------------------------------------------ calibration
    def _calibrate(self, now: float):
        elapsed = now - self._step_start
        # Collect the wrist x during the last CALIB_SAMPLE_S of the countdown.
        if elapsed >= config.CALIB_COUNTDOWN_S - config.CALIB_SAMPLE_S and self.has_hand:
            self._samples.append(self._reading.wrist_x)
        if elapsed < config.CALIB_COUNTDOWN_S:
            return

        if not self._samples:
            self._restart_step("no hand seen, try again")
            return
        value = sum(self._samples) / len(self._samples)
        if self.step == CALIB_LEFT:
            self.left_x = value
            self.step = CALIB_RIGHT
            self._restart_step("")
        else:
            span = value - self.left_x
            if span < config.CALIB_MIN_SPAN:
                # The range must cover enough of the camera image: a narrow range
                # makes tiny wrist moves sweep the paddle across the whole table.
                # (In the mirrored image, your left is the image's left.)
                self.start_calibration()
                if span <= 0:
                    self._calib_note = "right edge wasn't right of the left one: try again"
                else:
                    self._calib_note = (f"range only {span * 100:.0f}% of the camera width "
                                        f"(need {config.CALIB_MIN_SPAN * 100:.0f}%): "
                                        f"stand centered, reach farther")
                print(f"Calibration rejected: left={self.left_x:.2f} right={value:.2f} "
                      f"({span * 100:.0f}% of the frame, need {config.CALIB_MIN_SPAN * 100:.0f}%)")
                return
            self.right_x = value
            self.step = READY
            print(f"Calibrated: left={self.left_x:.2f} right={self.right_x:.2f} (of frame width)")

    def _restart_step(self, note: str):
        self._step_start = time.monotonic()
        self._samples = []
        self._calib_note = note
