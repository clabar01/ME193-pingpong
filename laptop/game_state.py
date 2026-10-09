"""Game rules: ball physics, the hit rule, the state machine, and the streaks.

No pygame in this file, on purpose: it only does math on numbers, so it can be
tested on its own and doesn't care where the paddle position or the swing come
from (keyboard now; camera and IMU later).

Coordinates are screen pixels: x grows to the right, y grows downward. The
player's side (the paddle) is at the bottom; the far wall is the top.
"""
import math
import random
from dataclasses import dataclass

import config

# States of the game.
#   WAITING: before a game starts (waiting for Space / an AprilTag)
#   PLAYING: the ball is in play
#   MISS:    the ball got past the paddle; pause, then serve again
WAITING = "WAITING"
PLAYING = "PLAYING"
MISS = "MISS"

# Events returned by GameState.update(), for sounds and MQTT to react to later.
EVENT_HIT = "hit"
EVENT_MISS = "miss"
EVENT_RECORD = "record"   # best_streak just went up
EVENT_SERVE = "serve"
EVENT_BEST_RESET = "best_reset"   # best_streak was reset (AprilTag 5)


@dataclass
class Ball:
    x: float
    y: float
    vx: float   # px/s
    vy: float   # px/s (positive = toward the player)
    radius: float = config.BALL_RADIUS


@dataclass
class Paddle:
    x: float        # center
    y: float        # top edge (the line the ball must reach)
    width: float
    hit_zone: float  # width of the hit zone, centered on x


def is_hit(ball: Ball, paddle: Paddle, swing_ok: bool) -> bool:
    """THE HIT RULE. Called once, at the moment the ball reaches the paddle's line.

    A hit counts only if BOTH are true:

      1. Position: the paddle is under the ball. The ball's center must be
         inside the hit zone, a band hit_zone pixels wide centered on the
         paddle (it's |ball.x - paddle.x| <= hit_zone / 2). hit_zone comes from
         the level (config.LEVELS): wider on easy levels, narrower on hard ones.

      2. Timing: swing_ok is True, meaning the player swung the real paddle
         close enough in time to this moment. For now the keyboard has no
         swing, so the caller always passes True. Later the accelerometer
         (IMU) on the paddle decides it: swing_ok will be True only if a swing
         event arrived within a short timing window around this moment.

    If either is false it's a miss and the rally ends.
    """
    in_zone = abs(ball.x - paddle.x) <= paddle.hit_zone / 2
    return in_zone and swing_ok


class GameState:
    def __init__(self, width: int = config.WINDOW_WIDTH, height: int = config.WINDOW_HEIGHT):
        self.width = width
        self.height = height
        self.state = WAITING
        self.level = config.DEFAULT_LEVEL
        self.streak = 0          # consecutive hits in the current rally
        self.best_streak = 0     # record (later: published to MQTT when it changes)
        self.miss_timer = 0.0    # seconds left in the MISS pause
        self.paddle = Paddle(
            x=width / 2,
            y=height - config.PADDLE_Y_FROM_BOTTOM,
            width=config.PADDLE_WIDTH,
            hit_zone=config.LEVELS[self.level]["hit_zone"],
        )
        self.ball = Ball(x=width / 2, y=height / 3, vx=0.0, vy=0.0)

    # ------------------------------------------------------------ controls
    @property
    def ball_speed(self) -> float:
        return config.LEVELS[self.level]["ball_speed"]

    def set_level(self, level: int):
        """Pick a level (only while WAITING)."""
        if self.state == WAITING and level in config.LEVELS:
            self.level = level
            self.paddle.hit_zone = config.LEVELS[level]["hit_zone"]

    def start(self) -> list:
        """WAITING -> PLAYING. Returns events."""
        if self.state != WAITING:
            return []
        self.streak = 0
        self.state = PLAYING
        self._serve()
        return [EVENT_SERVE]

    def reset_best(self) -> list:
        """Reset the record. Mid-rally, the record restarts from the current streak."""
        self.best_streak = self.streak
        return [EVENT_BEST_RESET]

    def set_paddle_x(self, x: float):
        """Move the paddle's center to x (clamped so it stays on screen)."""
        half = self.paddle.width / 2
        self.paddle.x = min(max(x, half), self.width - half)

    # ------------------------------------------------------------ update
    def update(self, dt: float, swing_ok: bool = True) -> list:
        """Advance the game by dt seconds. Returns a list of events (EVENT_*)."""
        dt = min(dt, config.MAX_DT)
        events = []

        if self.state == MISS:
            self._move_ball(dt)   # let the missed ball fly off the screen
            self.miss_timer -= dt
            if self.miss_timer <= 0:
                self.state = PLAYING
                self._serve()
                events.append(EVENT_SERVE)
            return events

        if self.state != PLAYING:
            return events

        ball, paddle = self.ball, self.paddle
        bottom_before = ball.y + ball.radius
        self._move_ball(dt)
        bottom_after = ball.y + ball.radius

        # Did the ball reach the paddle's line during this frame?
        reached_paddle = ball.vy > 0 and bottom_before < paddle.y <= bottom_after
        if reached_paddle:
            if is_hit(ball, paddle, swing_ok):
                self._bounce_off_paddle()
                self.streak += 1
                events.append(EVENT_HIT)
                if self.streak > self.best_streak:
                    self.best_streak = self.streak
                    events.append(EVENT_RECORD)
            else:
                self.state = MISS
                self.miss_timer = config.MISS_PAUSE_S
                self.streak = 0
                events.append(EVENT_MISS)
        return events

    # ------------------------------------------------------------ helpers
    def _serve(self):
        """New ball at the far wall, aimed at the player at a random angle."""
        angle = math.radians(random.uniform(-config.SERVE_ANGLE_DEG, config.SERVE_ANGLE_DEG))
        self.ball = Ball(
            x=self.width / 2,
            y=config.BALL_RADIUS + 1,
            vx=self.ball_speed * math.sin(angle),
            vy=self.ball_speed * math.cos(angle),
        )

    def _move_ball(self, dt: float):
        """Move the ball, bouncing off the side walls and the far (top) wall."""
        b = self.ball
        b.x += b.vx * dt
        b.y += b.vy * dt
        if b.x - b.radius < 0:
            b.x = b.radius
            b.vx = abs(b.vx)
        elif b.x + b.radius > self.width:
            b.x = self.width - b.radius
            b.vx = -abs(b.vx)
        if b.y - b.radius < 0:
            b.y = b.radius
            b.vy = abs(b.vy)
        # No bottom wall: past the paddle is a miss.

    def _bounce_off_paddle(self):
        """Send the ball back up, angled by where it hit the hit zone."""
        b, p = self.ball, self.paddle
        b.y = p.y - b.radius   # sit on the paddle's line, not inside it
        # -1 at the left edge of the hit zone, 0 at the center, +1 at the right edge
        offset = max(-1.0, min(1.0, (b.x - p.x) / (p.hit_zone / 2)))
        angle = math.radians(offset * config.MAX_BOUNCE_ANGLE_DEG)
        b.vx = self.ball_speed * math.sin(angle)
        b.vy = -self.ball_speed * math.cos(angle)
