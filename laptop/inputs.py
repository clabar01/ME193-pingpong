"""Input sources: where the paddle position, the swing, and "start" come from.

game.py only talks to these three small interfaces, so a camera or IMU source
can replace a keyboard one later without changing the game logic:

  paddle source:  update(dt); .x -> paddle center in screen pixels;
                  .ready (may the game start?), .message (big on-screen text or
                  None), .has_hand, handle_event(event),
                  draw_on_preview(surface) (extras on the camera view), stop()
                  (KeyboardPaddle here, pose_input.CameraPaddle for the camera)
  swing source:   swing_ok(now) -> bool, asked at the moment the ball reaches the paddle
  control source: poll() -> list of (action, value):
                    ("level", n)    show level n (only acted on while WAITING)
                    ("start", n)    start the game (n = level, or None = current)
                    ("reset", None) reset best_streak
                  (KeyboardStart here, apriltag_input.TagStart for AprilTags)

Keyboard sources always stay available as the fallback (no camera / no UNO Q).
"""
import pygame

import config


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
    """Placeholder swing source: every hit attempt counts as swung.

    Later replaced by an IMU source that answers True only if a swing event
    from the UNO Q arrived within the timing window around `now`.
    """

    def swing_ok(self, now: float) -> bool:
        return True


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
