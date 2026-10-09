"""The one webcam thread. Pose tracking and AprilTag detection share it.

The camera is opened exactly once. Each frame, the thread runs every
registered processor (pose_input.PoseProcessor, apriltag_input.TagProcessor)
on that same frame, then stores a small mirrored preview with everything they
drew on it. The game loop never waits: it just reads the newest results.

A processor is any object with:
    setup()                            load models (runs in the camera thread)
    process(rgb, preview, now)         rgb: original frame (NOT mirrored)
                                       preview: mirrored copy to draw on
    close()
Processors detect on the original frame and mirror their own x coordinates
(x -> 1 - x) for drawing and for the game, so the view feels like a mirror.
"""
import threading
import time
from typing import Optional

import cv2
import numpy as np
import pygame

import config


class Camera:
    def __init__(self, processors):
        self.processors = list(processors)
        self.error: Optional[str] = None   # set if the camera or a model fails
        self._lock = threading.Lock()
        self._preview: Optional[np.ndarray] = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)

    def preview_surface(self) -> Optional[pygame.Surface]:
        """Newest preview (skeleton, tags) as a pygame Surface, or None."""
        with self._lock:
            frame = self._preview
        if frame is None:
            return None
        h, w, _ = frame.shape
        return pygame.image.frombuffer(frame.tobytes(), (w, h), "RGB").copy()

    def _run(self):
        cap = cv2.VideoCapture(config.CAMERA_INDEX)
        if not cap.isOpened():
            self.error = (f"Can't open camera {config.CAMERA_INDEX}. Check macOS camera "
                          "permission for VS Code/Terminal, or CAMERA_INDEX in config.py.")
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
        try:
            for p in self.processors:
                p.setup()
        except Exception as e:
            self.error = f"Camera setup failed: {e}"
            cap.release()
            return

        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                preview = cv2.flip(rgb, 1)   # mirror view
                now = time.monotonic()
                for p in self.processors:
                    p.process(rgb, preview, now)
                h, w, _ = preview.shape
                pw = config.CAMERA_PREVIEW_WIDTH
                small = cv2.resize(preview, (pw, int(h * pw / w)))
                with self._lock:
                    self._preview = small
        finally:
            cap.release()
            for p in self.processors:
                p.close()
