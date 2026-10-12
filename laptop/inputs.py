"""Input sources: where the paddle position, the swing, and "start" come from.

game.py only talks to these three small interfaces, so a camera or IMU source
can replace a keyboard one later without changing the game logic:

  paddle source:  update(dt); .x, .y -> paddle center in the hitting plane (m);
                  .ready (may the game start?), .message (big on-screen text or
                  None), .has_hand, .tracking (how the paddle is found:
                  "keys", or the camera's wrist/hand/elbow/hold/lost),
                  handle_event(event),
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
    """Arrow keys move the paddle in the hitting plane: left/right and up/down."""

    kind = "keyboard"
    ready = True      # nothing to calibrate
    message = None
    has_hand = True   # a keyboard never loses track of the paddle
    tracking = "keys"

    def __init__(self, start_x: float = 0.0, start_y: float = 0.3):
        self.x = start_x   # meters across the table
        self.y = start_y   # meters above the table

    def handle_event(self, event):
        pass

    def draw_on_preview(self, surf, debug=False):
        pass

    def stop(self):
        pass

    def update(self, dt: float):
        keys = pygame.key.get_pressed()
        self.x += (keys[pygame.K_RIGHT] - keys[pygame.K_LEFT]) * config.PADDLE_KEY_SPEED * dt
        self.y += (keys[pygame.K_UP] - keys[pygame.K_DOWN]) * config.PADDLE_KEY_SPEED * dt
        # Same limits as GameState, so the paddle responds at once after
        # being held against an edge.
        self.x = min(max(self.x, -config.PADDLE_X_RANGE_M), config.PADDLE_X_RANGE_M)
        self.y = min(max(self.y, config.PADDLE_Y_MIN_M), config.PADDLE_Y_MAX_M)


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


# Each digit's key on the number row and on the keypad (keypad codes aren't in order)
DIGIT_KEYS = {0: (pygame.K_0, pygame.K_KP0), 1: (pygame.K_1, pygame.K_KP1), 2: (pygame.K_2, pygame.K_KP2),
              3: (pygame.K_3, pygame.K_KP3), 4: (pygame.K_4, pygame.K_KP4), 5: (pygame.K_5, pygame.K_KP5),
              6: (pygame.K_6, pygame.K_KP6), 7: (pygame.K_7, pygame.K_KP7), 8: (pygame.K_8, pygame.K_KP8),
              9: (pygame.K_9, pygame.K_KP9)}


class KeyboardStart:
    """Space starts the game at the current level; number keys 1/2/3
    (config.KEY_LEVELS) pick that level and start (like holding up a level
    card); S and L play the
    SLOW-MO and EXTRA LIFE cards (for testing without printed tags).

    The fallback for the AprilTag source (apriltag_input.TagStart), which
    produces the same actions.
    """

    LEVEL_KEYS = {key: level for digit, level in config.KEY_LEVELS.items() for key in DIGIT_KEYS[digit]}

    def __init__(self):
        self._actions = []

    def handle_event(self, event):
        if event.type != pygame.KEYDOWN:
            return
        if event.key == pygame.K_s:
            self._actions.append(("slowmo", None))       # stand-in for the SLOW-MO card (tag 10)
        elif event.key == pygame.K_l:
            self._actions.append(("extra_life", None))   # stand-in for the EXTRA LIFE card (tag 11)
        elif event.key == pygame.K_SPACE:
            self._actions.append(("start", None))   # None = keep the current level
        elif event.key in self.LEVEL_KEYS and self.LEVEL_KEYS[event.key] in config.LEVELS:
            self._actions.append(("start", self.LEVEL_KEYS[event.key]))   # pick that level and start

    def poll(self) -> list:
        """Actions since the last poll."""
        actions, self._actions = self._actions, []
        return actions
