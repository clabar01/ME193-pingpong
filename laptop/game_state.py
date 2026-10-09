"""Game rules: ball physics, the hit rule, the state machine, and the streaks.

No pygame in this file, on purpose: it only does math on numbers, so it can be
tested on its own and doesn't care where the paddle position or the swing come
from (keyboard, camera, IMU).

Coordinates are screen pixels: x grows to the right, y grows downward. The
player's side (the paddle) is at the bottom; the far wall is the top.
Times (`now`, contact and swing times) are time.monotonic() seconds.
"""
import math
import random
from dataclasses import dataclass
from typing import Optional

import config

# States of the game.
#   WAITING: before a game starts (waiting for Space / an AprilTag)
#   PLAYING: the ball is in play (including a short wait for a late swing)
#   MISS:    the ball got past the paddle; pause, then serve again
WAITING = "WAITING"
PLAYING = "PLAYING"
MISS = "MISS"

# Events returned by GameState.update(), for sounds and MQTT to react to.
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


@dataclass
class Swing:
    arrival: float                 # when the laptop got the swing (time.monotonic())
    peak: Optional[float] = None   # peak acceleration in g (|a| - 1 g), if measured


# =============================================================================
# THE HIT RULE
#
# A return counts as a hit only if all three of these are true:
#
#   1. The ball reaches the paddle. "Contact" is the moment the ball's lower
#      edge crosses the paddle's line. Nothing is judged before that.
#
#   2. The paddle is in the right place AT CONTACT. The camera tracks the
#      player's wrist, which moves the paddle. At the moment of contact the
#      ball's center must be inside the hit zone: a band centered on the paddle
#      whose width depends on the level (wider on Easy, narrower on Hard).
#      If it isn't, it's a miss right away. Moving the paddle afterwards
#      doesn't help.
#
#   3. The player really swung, at the right time. The accelerometer and
#      gyroscope on the real paddle send a "swing" message (the UNO Q only
#      sends one for a move that is both hard enough and turning fast enough).
#      These messages travel over the internet, so they arrive on the laptop
#      late: we measured 110 to 485 ms. So the swing is judged by when it
#      ARRIVES, and the timing window is lopsided: a swing may arrive up to
#      SWING_WINDOW_BEFORE_S (0.15 s) before contact, or up to
#      SWING_WINDOW_AFTER_S (0.55 s) after it. Each swing can only be used
#      for one hit.
#
# Because a swing can arrive after contact, the decision may have to wait.
# While it waits, the ball presses into the paddle and slows to a stop (it
# looks like the paddle cushioning the ball). Then:
#   - a swing arrives inside the window  -> HIT: the ball flies back, faster
#     the harder the swing was;
#   - the window closes with no swing    -> MISS: the ball drops through.
#
# Why this is fair: a swing alone never scores (the paddle must be in place at
# contact), and the paddle alone never scores (there must be a real swing
# close to contact). An occasional false swing from the sensor only matters if
# it lands in that short window while the paddle is already in the right spot.
#
# With the keyboard (Space = swing) or --no-imu (every ball counts as swung),
# the swing arrives instantly, so check 3 is decided at contact.
# =============================================================================
def is_hit(ball: Ball, paddle: Paddle, contact_time: Optional[float],
           swing: Optional[Swing], now: float) -> Optional[bool]:
    """Apply the hit rule above.

    ball, paddle: where they were AT CONTACT (contact_time).
    swing: the latest unused swing, or None.
    Returns True (hit), False (miss), or None (not decided yet: keep waiting).
    """
    # 1. The ball hasn't reached the paddle yet: nothing to decide.
    if contact_time is None:
        return None

    # 2. Paddle position at contact.
    if abs(ball.x - paddle.x) > paddle.hit_zone / 2:
        return False

    # 3. A swing that arrived inside the timing window around contact.
    opens = contact_time - config.SWING_WINDOW_BEFORE_S
    closes = contact_time + config.SWING_WINDOW_AFTER_S
    if swing is not None and opens <= swing.arrival <= closes:
        return True
    if now > closes:
        return False   # window closed without a swing
    return None        # window still open: wait


def return_speed_factor(peak: Optional[float]) -> float:
    """Harder swing = faster return (see RETURN_* in config.py)."""
    if peak is None:
        return 1.0
    lo, hi = config.RETURN_PEAK_LOW_G, config.RETURN_PEAK_HIGH_G
    frac = min(max((peak - lo) / (hi - lo), 0.0), 1.0)
    return config.RETURN_FACTOR_MIN + frac * (config.RETURN_FACTOR_MAX - config.RETURN_FACTOR_MIN)


class GameState:
    def __init__(self, width: int = config.WINDOW_WIDTH, height: int = config.WINDOW_HEIGHT):
        self.width = width
        self.height = height
        self.state = WAITING
        self.level = config.DEFAULT_LEVEL
        self.streak = 0          # consecutive hits in the current rally
        self.best_streak = 0     # record (published to MQTT when it changes)
        self.miss_timer = 0.0    # seconds left in the MISS pause
        self.paddle = Paddle(
            x=width / 2,
            y=height - config.PADDLE_Y_FROM_BOTTOM,
            width=config.PADDLE_WIDTH,
            hit_zone=config.LEVELS[self.level]["hit_zone"],
        )
        self.ball = Ball(x=width / 2, y=height / 3, vx=0.0, vy=0.0)

        # Contact / waiting for a swing (see the hit rule)
        self.holding = False          # ball pressed into the paddle, waiting for a swing
        self.contact_time = None      # when the ball reached the paddle line
        self._contact_ball = None     # ball and paddle as they were at contact
        self._contact_paddle = None
        self._held_velocity = (0.0, 0.0)
        self._used_swing = None       # arrival time of the swing used for the last hit
        # For the screen: the last hit's swing timing and return speed
        self.last_swing_offset = None   # swing arrival - contact (s), for the last hit
        self.last_return_factor = None

    # ------------------------------------------------------------ controls
    @property
    def ball_speed(self) -> float:
        return config.LEVELS[self.level]["ball_speed"]

    @property
    def hold_progress(self) -> float:
        """0..1: how much of the post-contact swing window has passed (for drawing)."""
        if not self.holding or self.contact_time is None or self._now is None:
            return 0.0
        return min(1.0, (self._now - self.contact_time) / config.SWING_WINDOW_AFTER_S)

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
    _now = None

    def update(self, dt: float, now: float, swing: Optional[Swing] = None) -> list:
        """Advance the game by dt seconds. `now` = time.monotonic(); `swing` =
        the latest swing from the swing source (or None). Returns events."""
        dt = min(dt, config.MAX_DT)
        self._now = now
        events = []
        if swing is not None and swing.arrival == self._used_swing:
            swing = None   # already used for a hit: each swing counts once

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

        if self.holding:
            # Ball pressed into the paddle: slow it down smoothly, keep direction.
            decay = math.exp(-dt / config.HOLD_DECAY_S)
            self.ball.vx *= decay
            self.ball.vy *= decay
            self.ball.x += self.ball.vx * dt
            self.ball.y += self.ball.vy * dt
        else:
            bottom_before = self.ball.y + self.ball.radius
            self._move_ball(dt)
            bottom_after = self.ball.y + self.ball.radius
            # Check 1: did the ball reach the paddle's line during this frame?
            if self.ball.vy > 0 and bottom_before < self.paddle.y <= bottom_after:
                self.contact_time = now
                self._contact_ball = Ball(self.ball.x, self.ball.y, self.ball.vx, self.ball.vy)
                self._contact_paddle = Paddle(self.paddle.x, self.paddle.y,
                                              self.paddle.width, self.paddle.hit_zone)
                self._held_velocity = (self.ball.vx, self.ball.vy)
                self.holding = True

        if not self.holding:
            return events

        verdict = is_hit(self._contact_ball, self._contact_paddle, self.contact_time, swing, now)
        if verdict is None:
            return events                     # keep waiting for a late swing
        self.holding = False
        if verdict:
            self._used_swing = swing.arrival
            self.last_swing_offset = swing.arrival - self.contact_time
            self.last_return_factor = return_speed_factor(swing.peak)
            self._bounce_off_paddle(self.last_return_factor)
            self.streak += 1
            events.append(EVENT_HIT)
            if self.streak > self.best_streak:
                self.best_streak = self.streak
                events.append(EVENT_RECORD)
        else:
            self.ball.vx, self.ball.vy = self._held_velocity   # drop through the paddle
            self.state = MISS
            self.miss_timer = config.MISS_PAUSE_S
            self.streak = 0
            events.append(EVENT_MISS)
        self.contact_time = None
        return events

    # ------------------------------------------------------------ helpers
    def _serve(self):
        """New ball at the far wall, aimed at the player at a random angle."""
        self.holding = False
        self.contact_time = None
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
            # A hard swing only speeds up the trip back; off the far wall the
            # ball returns at the level's speed (same direction).
            speed = math.hypot(b.vx, b.vy)
            if speed > 0:
                b.vx *= self.ball_speed / speed
                b.vy *= self.ball_speed / speed
        # No bottom wall: past the paddle is a miss.

    def _bounce_off_paddle(self, speed_factor: float = 1.0):
        """Send the ball back up, angled by where it hit the hit zone, at the
        level's speed times speed_factor (harder swing = faster)."""
        b, p = self.ball, self._contact_paddle or self.paddle
        b.y = self.paddle.y - b.radius   # back on the paddle's line, out of the cushion
        # -1 at the left edge of the hit zone, 0 at the center, +1 at the right edge
        offset = max(-1.0, min(1.0, (b.x - p.x) / (p.hit_zone / 2)))
        angle = math.radians(offset * config.MAX_BOUNCE_ANGLE_DEG)
        speed = self.ball_speed * speed_factor
        b.vx = speed * math.sin(angle)
        b.vy = -speed * math.cos(angle)
