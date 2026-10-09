"""Input sources: where the paddle position, the swing, and "start" come from.

game.py only talks to these three small interfaces, so a camera or IMU source
can replace a keyboard one later without changing the game logic:

  paddle source:  update(dt); .x -> paddle center in screen pixels;
                  .ready (may the game start?), .message (big on-screen text or
                  None), .has_hand, handle_event(event),
                  draw_on_preview(surface) (extras on the camera view), stop()
                  (KeyboardPaddle here, pose_input.CameraPaddle for the camera)
  swing source:   last_swing(now) -> latest game_state.Swing (arrival time, peak) or
                  None; handle_event(event). The hit rule (game_state.is_hit)
                  decides whether it falls in the timing window.
  control source: poll() -> list of (action, value):
                    ("level", n)    show level n (only acted on while WAITING)
                    ("start", n)    start the game (n = level, or None = current)
                    ("reset", None) reset best_streak
                  (KeyboardStart here, apriltag_input.TagStart for AprilTags)

Keyboard sources always stay available as the fallback (no camera / no UNO Q).
"""
import time

import pygame

import config
from game_state import Swing


class KeyboardPaddle:
    """Left/Right arrow keys slide the paddle."""

    kind = "keyboard"
    ready = True      # nothing to calibrate
    message = None
    has_hand = True   # a keyboard never loses track of the paddle

    def __init__(self, start_x: float = config.WINDOW_WIDTH / 2):
        self.x = start_x

    def handle_event(self, event):
        pass

    def draw_on_preview(self, surf):
        pass

    def stop(self):
        pass

    def update(self, dt: float):
        keys = pygame.key.get_pressed()
        direction = keys[pygame.K_RIGHT] - keys[pygame.K_LEFT]
        self.x += direction * config.PADDLE_KEY_SPEED * dt
        # Same limits as GameState, so the paddle responds at once after
        # being held against a wall.
        half = config.PADDLE_WIDTH / 2
        self.x = min(max(self.x, half), config.WINDOW_WIDTH - half)


class AlwaysSwing:
    """--no-imu: every ball counts as swung (the swing "arrives" at contact)."""

    kind = "always"

    def handle_event(self, event):
        pass

    def last_swing(self, now: float) -> Swing:
        return Swing(arrival=now)


class KeyboardSwing:
    """Space = swing, for the keyboard paddle (the keyboard fallback must stay playable)."""

    kind = "keyboard"

    def __init__(self):
        self._last = None

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
            self._last = Swing(arrival=time.monotonic())

    def last_swing(self, now: float):
        return self._last


class ImuSwing:
    """Swings from the paddle's IMU (UNO Q paddle_imu), received over MQTT by
    mqtt_client.GameMqtt, which stores the latest one with its arrival time."""

    kind = "imu"

    def __init__(self, mqtt):
        self.mqtt = mqtt

    def handle_event(self, event):
        pass

    def last_swing(self, now: float):
        return self.mqtt.last_swing


class LatestSwing:
    """Combines swing sources: the most recent swing from any of them."""

    def __init__(self, *sources):
        self.sources = sources
        self.kind = "+".join(s.kind for s in sources)

    def handle_event(self, event):
        for s in self.sources:
            s.handle_event(event)

    def last_swing(self, now: float):
        swings = [s.last_swing(now) for s in self.sources]
        swings = [s for s in swings if s is not None]
        return max(swings, key=lambda s: s.arrival) if swings else None


class KeyboardStart:
    """Space starts the game; number keys 0-2 pick the level.

    The fallback for the AprilTag source (apriltag_input.TagStart), which
    produces the same actions.
    """

    LEVEL_KEYS = {pygame.K_0: 0, pygame.K_1: 1, pygame.K_2: 2,
                  pygame.K_KP0: 0, pygame.K_KP1: 1, pygame.K_KP2: 2}

    def __init__(self):
        self._actions = []

    def handle_event(self, event):
        if event.type != pygame.KEYDOWN:
            return
        if event.key == pygame.K_SPACE:
            self._actions.append(("start", None))   # None = keep the current level
        elif event.key in self.LEVEL_KEYS and self.LEVEL_KEYS[event.key] in config.LEVELS:
            self._actions.append(("level", self.LEVEL_KEYS[event.key]))

    def poll(self) -> list:
        """Actions since the last poll."""
        actions, self._actions = self._actions, []
        return actions
