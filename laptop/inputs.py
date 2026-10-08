"""Input sources: where the paddle position, the swing, and "start" come from.

game.py only talks to these three small interfaces, so a camera or IMU source
can replace a keyboard one later without changing the game logic:

  paddle source:  update(dt); .x -> paddle center in screen pixels
  swing source:   swing_ok(now) -> bool, asked at the moment the ball reaches the paddle
  start source:   handle_event(event); poll() -> level to start at, or None

Keyboard sources always stay available as the fallback (no camera / no UNO Q).
"""
import pygame

import config


class KeyboardPaddle:
    """Left/Right arrow keys slide the paddle."""

    def __init__(self, start_x: float = config.WINDOW_WIDTH / 2):
        self.x = start_x

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
    """Space starts the game; number keys 0-2 pick the level while waiting.

    Later an AprilTag source does the same job: tag seen -> start at that level.
    """

    LEVEL_KEYS = {pygame.K_0: 0, pygame.K_1: 1, pygame.K_2: 2,
                  pygame.K_KP0: 0, pygame.K_KP1: 1, pygame.K_KP2: 2}

    def __init__(self):
        self.level = config.DEFAULT_LEVEL
        self._start_requested = False

    def handle_event(self, event):
        if event.type != pygame.KEYDOWN:
            return
        if event.key == pygame.K_SPACE:
            self._start_requested = True
        elif event.key in self.LEVEL_KEYS and self.LEVEL_KEYS[event.key] in config.LEVELS:
            self.level = self.LEVEL_KEYS[event.key]

    def poll(self):
        """Level to start at if Space was pressed since the last poll, else None."""
        if self._start_requested:
            self._start_requested = False
            return self.level
        return None
