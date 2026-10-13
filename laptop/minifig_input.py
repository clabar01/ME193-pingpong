"""Pick your opponent: hold a blue or green minifig up to the camera.

The detector is the YOLOv8 model trained for the door-to-door project
(github.com/clabar01/mini-fig-yolo, models/best.pt: YOLOv8n fine-tuned on
95 labeled photos, classes blue-minifig and green-minifig). It was NOT
retrained: the same weights were converted once to ONNX
(tools/export_minifig_onnx.py -> assets/models/minifig_yolov8n.onnx), so the
game runs them with OpenCV's built-in DNN module instead of installing
ultralytics + torch (and ultralytics' opencv-python would clash with the
opencv-contrib-python MediaPipe needs).

Three parts:

  MinifigDetector   the model alone: detect(rgb) -> {"blue": conf, "green": conf}
                    (no camera, so it can be tested on photos)
  MinifigProcessor  a camera processor (see camera.py). It only runs the model
                    while .active is set, i.e. on the opponent select screen;
                    during play it returns at once, so the game stays fast.
  OpponentPicker    turns detections into a choice: the same color seen
                    steadily for MINIFIG_HOLD_S (gaps up to MINIFIG_GAP_S are
                    forgiven) -> "blue" / "green".
"""
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

import config

REPO_ROOT = Path(__file__).resolve().parent.parent
CLASSES = ["blue", "green"]          # model class 0 = blue-minifig, 1 = green-minifig
BOX_COLORS = {"blue": (90, 150, 255), "green": (80, 220, 110)}   # RGB, on the camera view


class MinifigDetector:
    """YOLOv8 (ONNX) with OpenCV DNN, preprocessed exactly like ultralytics:
    letterbox to 640 x 640 with grey (114) padding, RGB, 0..1."""

    def __init__(self, path=None):
        path = REPO_ROOT / (path or config.MINIFIG_MODEL_PATH)
        if not path.exists():
            raise FileNotFoundError(f"no minifig model at {path.relative_to(REPO_ROOT)}")
        self.net = cv2.dnn.readNetFromONNX(str(path))
        self.size = config.MINIFIG_IMG_SIZE

    def detect(self, rgb: np.ndarray):
        """Best confidence per color (only those >= MINIFIG_CONF), and each
        one's box as fractions of the image (x0, y0, x1, y1)."""
        h, w = rgb.shape[:2]
        s = self.size
        scale = min(s / w, s / h)
        nw, nh = int(round(w * scale)), int(round(h * scale))
        px, py = (s - nw) // 2, (s - nh) // 2
        canvas = np.full((s, s, 3), 114, np.uint8)
        canvas[py:py + nh, px:px + nw] = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
        self.net.setInput(cv2.dnn.blobFromImage(canvas, 1 / 255.0))
        out = self.net.forward()[0]           # (4 + classes, 8400): cx, cy, w, h, class scores
        scores = out[4:]
        found, boxes = {}, {}
        for c, name in enumerate(CLASSES):
            i = int(np.argmax(scores[c]))
            conf = float(scores[c, i])
            if conf < config.MINIFIG_CONF:
                continue
            cx, cy, bw, bh = out[:4, i]
            x0 = (cx - bw / 2 - px) / scale / w
            y0 = (cy - bh / 2 - py) / scale / h
            x1 = (cx + bw / 2 - px) / scale / w
            y1 = (cy + bh / 2 - py) / scale / h
            found[name] = conf
            boxes[name] = (max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1))
        return found, boxes


class MinifigProcessor:
    """Camera processor (camera.py). Runs the model only while `active` is set
    (the opponent select screen), at most MINIFIG_HZ times a second."""

    def __init__(self):
        self.active = threading.Event()
        self.error: Optional[str] = None     # model couldn't load: keyboard / voice choice only
        self.ms = None                       # last inference time (ms), for the debug overlay
        self._detector = None
        self._lock = threading.Lock()
        self._latest = None                  # (found, boxes, time)
        self._last_run = 0.0

    def setup(self):
        # Never let a missing model break the camera (pose + tags still need it)
        try:
            self._detector = MinifigDetector()
        except Exception as e:
            self.error = f"minifig model not loaded ({e})"
            print(f"minifig: {self.error}; pick the opponent with B / G or by voice")

    def latest(self):
        with self._lock:
            return self._latest

    def process(self, rgb, preview, now):
        if not self.active.is_set() or self._detector is None:
            return                            # during play: nothing (keeps the game fast)
        if now - self._last_run < 1 / config.MINIFIG_HZ:
            self._draw(preview)
            return
        self._last_run = now
        t0 = time.perf_counter()
        try:
            found, boxes = self._detector.detect(rgb)
        except Exception as e:
            self.error = f"minifig model failed ({e})"
            print(f"minifig: {self.error}")
            self._detector = None
            return
        self.ms = (time.perf_counter() - t0) * 1000
        with self._lock:
            self._latest = (found, boxes, now)
        self._draw(preview)

    def _draw(self, preview):
        latest = self.latest()
        if latest is None or time.monotonic() - latest[2] > 0.5:
            return
        found, boxes, _ = latest
        h, w = preview.shape[:2]
        for name, (x0, y0, x1, y1) in boxes.items():
            # The preview is mirrored: x -> 1 - x
            p0 = (int((1 - x1) * w), int(y0 * h))
            p1 = (int((1 - x0) * w), int(y1 * h))
            cv2.rectangle(preview, p0, p1, BOX_COLORS[name], 3)
            cv2.putText(preview, f"{name} {found[name]:.2f}", (p0[0], max(18, p0[1] - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, BOX_COLORS[name], 2)

    def close(self):
        pass


class OpponentPicker:
    """Steady-detection rule: a color counts once it has been seen for
    MINIFIG_HOLD_S without a gap longer than MINIFIG_GAP_S (a frame or two
    without a detection doesn't reset it). If both colors are in view, the
    more confident one counts; switching colors starts the hold over."""

    def __init__(self, processor: Optional[MinifigProcessor]):
        self.processor = processor
        self.color = None          # color being held up now
        self.since = None          # when it was first seen (this hold)
        self.last_seen = None

    @property
    def available(self) -> bool:
        return self.processor is not None and self.processor.error is None

    def reset(self):
        self.color = self.since = self.last_seen = None

    def progress(self, now: float) -> float:
        """0..1 of the hold, for the progress bar."""
        if self.color is None:
            return 0.0
        return min(1.0, (now - self.since) / config.MINIFIG_HOLD_S)

    def update(self, now: float) -> Optional[str]:
        """Call every frame on the select screen; returns the chosen color once."""
        latest = self.processor.latest() if self.available else None
        seen = None
        if latest is not None and now - latest[2] < config.MINIFIG_GAP_S:
            found = latest[0]
            if found:
                seen = max(found, key=found.get)
        return self.feed(seen, now)

    def feed(self, seen: Optional[str], now: float) -> Optional[str]:
        """The rule itself (testable without a camera): seen = color in the
        newest detection, or None."""
        if seen is not None:
            if seen != self.color:
                self.color, self.since = seen, now
            self.last_seen = now
        elif self.color is not None and now - self.last_seen > config.MINIFIG_GAP_S:
            self.reset()
        if self.color is not None and now - self.since >= config.MINIFIG_HOLD_S:
            chosen = self.color
            self.reset()
            return chosen
        return None
