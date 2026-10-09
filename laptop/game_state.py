"""Game rules: 3D ball physics, the hit rule, the opponent, and the streaks.

No pygame in this file, on purpose: it only does math on numbers, so it can be
tested on its own and doesn't care where the paddle position or the swing come
from (keyboard, camera, IMU), or how things are drawn (view3d.py, draw.py).

World coordinates are meters (see config.py):
    x  across the table (0 = center, + = right)
    y  height above the table surface
    z  along the table (0 = MY end, TABLE_LENGTH = the opponent's end)
Times (`now`, contact and swing times) are time.monotonic() seconds.

A rally: the opponent serves/returns -> the ball arcs over the net, bounces
once on my side -> it reaches my hitting plane ("contact") -> the hit rule
decides -> on a hit the ball arcs back over the net, bounces once on the
opponent's side -> the opponent (a simple AI) returns it or misses.
"""
import math
import random
from dataclasses import dataclass
from typing import Optional

import config

# States of the game.
#   WAITING: before a game starts (waiting for Space / an AprilTag)
#   PLAYING: the ball is in play (including a short wait for a late swing)
#   MISS:    I missed; pause, then a new serve (my streak resets)
#   POINT:   the opponent missed; pause, then a new serve (my streak continues)
WAITING = "WAITING"
PLAYING = "PLAYING"
MISS = "MISS"
POINT = "POINT"

# Events returned by GameState.update(), for sounds and MQTT to react to.
EVENT_HIT = "hit"
EVENT_MISS = "miss"
EVENT_RECORD = "record"           # best_streak just went up
EVENT_SERVE = "serve"
EVENT_BEST_RESET = "best_reset"   # best_streak was reset (AprilTag 5)
EVENT_BOUNCE = "bounce"           # ball bounced on the table
EVENT_OPP_HIT = "opp_hit"         # the opponent returned the ball
EVENT_POINT = "point"             # the opponent missed


@dataclass
class Ball:
    x: float
    y: float
    z: float
    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0   # + = toward the opponent


@dataclass
class Paddle:
    x: float   # center, in my hitting plane (z = MY_HIT_Z)
    y: float


@dataclass
class Swing:
    arrival: float                 # when the laptop got the swing (time.monotonic())
    peak: Optional[float] = None   # peak acceleration in g (|a| - 1 g), if measured


# =============================================================================
# THE HIT RULE
#
# A return counts as a hit only if all three of these are true:
#
#   1. The ball reaches my paddle. The ball comes over the net, bounces once on
#      my side of the table, and then reaches my hitting plane: an invisible
#      wall just behind my end of the table where my paddle moves. "Contact"
#      is the moment the ball crosses that plane. Nothing is judged before.
#
#   2. The paddle is in the right place AT CONTACT. The camera tracks the
#      player's wrist, which moves the paddle left/right and up/down in the
#      hitting plane. At the moment of contact the paddle must overlap the
#      ball: the distance between the ball's center and the paddle's center
#      must be at most paddle radius + ball radius + a tolerance that depends
#      on the level (generous on Beginner, tight on Pro). If not, it's a miss
#      right away. Moving the paddle afterwards doesn't help.
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
#   - a swing arrives inside the window  -> HIT: the ball flies back over the
#     net, faster and deeper the harder the swing was;
#   - the window closes with no swing    -> MISS: the ball drops past me.
#
# Why this is fair: a swing alone never scores (the paddle must be on the
# ball at contact), and the paddle alone never scores (there must be a real
# swing close to contact). An occasional false swing from the sensor only
# matters if it lands in that short window while the paddle is already on
# the ball.
#
# With the keyboard (Space = swing) or --no-imu (every ball counts as swung),
# the swing arrives instantly, so check 3 is decided at contact.
# =============================================================================
def is_hit(ball: Ball, paddle: Paddle, tolerance: float, contact_time: Optional[float],
           swing: Optional[Swing], now: float) -> Optional[bool]:
    """Apply the hit rule above.

    ball, paddle: where they were AT CONTACT (contact_time).
    tolerance: the level's hit_tolerance (m).
    swing: the latest unused swing, or None.
    Returns True (hit), False (miss), or None (not decided yet: keep waiting).
    """
    # 1. The ball hasn't reached my hitting plane yet: nothing to decide.
    if contact_time is None:
        return None

    # 2. Paddle on the ball at contact (distance in the hitting plane).
    reach = config.PADDLE_RADIUS_M + config.BALL_RADIUS_M + tolerance
    if math.hypot(ball.x - paddle.x, ball.y - paddle.y) > reach:
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
    """Harder swing = faster return (see RETURN_* in config.py). 1.0 if unmeasured."""
    if peak is None:
        return 1.0
    lo, hi = config.RETURN_PEAK_LOW_G, config.RETURN_PEAK_HIGH_G
    frac = min(max((peak - lo) / (hi - lo), 0.0), 1.0)
    return config.RETURN_FACTOR_MIN + frac * (config.RETURN_FACTOR_MAX - config.RETURN_FACTOR_MIN)


def aim(ball: Ball, target_x: float, target_z: float, speed: float):
    """Set the ball's velocity so it flies from where it is to land on the
    table at (target_x, target_z), moving along the table at about `speed`
    m/s, and clears the net. If the straight-ish arc would hit the net, the
    flight time is stretched (a higher, slower arc) until it clears."""
    flight = max(abs(target_z - ball.z) / speed, 0.15)
    net_z = config.TABLE_LENGTH / 2
    for _ in range(30):
        vx = (target_x - ball.x) / flight
        vz = (target_z - ball.z) / flight
        vy = (0.0 - ball.y + 0.5 * config.GRAVITY * flight ** 2) / flight
        t_net = (net_z - ball.z) / vz if vz != 0 else -1
        if t_net <= 0:
            break   # doesn't cross the net
        y_net = ball.y + vy * t_net - 0.5 * config.GRAVITY * t_net ** 2
        if y_net >= config.NET_HEIGHT + config.BALL_RADIUS_M + config.NET_CLEARANCE:
            break
        flight *= 1.1
    ball.vx, ball.vy, ball.vz = vx, vy, vz


def on_table(x: float, z: float) -> bool:
    return abs(x) <= config.TABLE_WIDTH / 2 and 0.0 <= z <= config.TABLE_LENGTH


class Opponent:
    """A simple AI at the far end. It watches my return, waits its reaction
    time, then slides its paddle (at most max_speed) toward where it thinks
    the ball will cross its hitting plane, plus a random aim error. It
    returns the ball if its paddle is within OPP_REACH_M of the ball there."""

    def __init__(self):
        self.x = 0.0
        self.target_x = 0.0
        self.react_at = 0.0     # game time it starts moving
        self.params = config.LEVELS[config.DEFAULT_LEVEL]["opponent"]

    def watch(self, ball: Ball, game_time: float):
        """Called when I return the ball: predict where it will cross, add error."""
        cross = predict_crossing(ball, config.OPP_HIT_Z)
        guess = cross if cross is not None else 0.0
        self.target_x = guess + random.gauss(0.0, self.params["aim_error_m"])
        self.react_at = game_time + self.params["reaction_s"]

    def step(self, dt: float, game_time: float):
        if game_time < self.react_at:
            return
        step = self.params["max_speed"] * dt
        self.x += max(-step, min(step, self.target_x - self.x))

    def go_home(self, game_time: float):
        self.target_x, self.react_at = 0.0, game_time


def step_ball(ball: Ball, dt: float) -> bool:
    """Move the ball one time step under gravity, bouncing on the table.
    Returns True if it bounced this step."""
    ball.vy -= config.GRAVITY * dt
    ball.x += ball.vx * dt
    ball.y += ball.vy * dt
    ball.z += ball.vz * dt
    if ball.y <= 0.0 and ball.vy < 0 and on_table(ball.x, ball.z):
        ball.y = -ball.y
        ball.vy = -ball.vy * config.BOUNCE_RESTITUTION
        return True
    return False


def predict_crossing(ball: Ball, plane_z: float, dt: float = 1 / 240) -> Optional[float]:
    """Simulate ahead: the ball's x when it crosses z = plane_z (or None)."""
    b = Ball(ball.x, ball.y, ball.z, ball.vx, ball.vy, ball.vz)
    for _ in range(int(5 / dt)):
        before = b.z
        step_ball(b, dt)
        if (before - plane_z) * (b.z - plane_z) <= 0 and before != b.z:
            return b.x
        if b.y < -1.0:
            return None
    return None


class GameState:
    def __init__(self):
        self.state = WAITING
        self.level = config.DEFAULT_LEVEL
        self.streak = 0          # my consecutive successful returns
        self.best_streak = 0     # record (published to MQTT when it changes)
        self.pause_timer = 0.0   # seconds left in a MISS / POINT pause
        self.paddle = Paddle(0.0, (config.PADDLE_Y_MIN_M + config.PADDLE_Y_MAX_M) / 2)
        self.ball = Ball(0.0, config.SERVE_HEIGHT, config.OPP_HIT_Z)
        self.opponent = Opponent()
        self.game_time = 0.0     # seconds of play (drives the opponent)

        # Rally bookkeeping
        self.coming_to_me = True      # ball heading my way (else to the opponent)
        self.bounced_my_side = False
        self.bounced_opp_side = False
        self.last_bounce = None       # (x, z, game_time) of the last bounce, for drawing

        # Contact / waiting for a swing (see the hit rule)
        self.holding = False
        self.contact_time = None
        self._contact_ball = None
        self._contact_paddle = None
        self._held_velocity = (0.0, 0.0, 0.0)
        self._used_swing = None
        self._now = None
        # For the screen: the last hit's swing timing and return speed
        self.last_swing_offset = None
        self.last_return_factor = None

    # ------------------------------------------------------------ controls
    @property
    def params(self) -> dict:
        return config.LEVELS[self.level]

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
            self.opponent.params = config.LEVELS[level]["opponent"]

    def start(self) -> list:
        """WAITING -> PLAYING. Returns events."""
        if self.state != WAITING:
            return []
        self.streak = 0
        self.opponent.params = self.params["opponent"]
        self._serve()
        return [EVENT_SERVE]

    def reset_best(self) -> list:
        """Reset the record. Mid-rally, the record restarts from the current streak."""
        self.best_streak = self.streak
        return [EVENT_BEST_RESET]

    def set_paddle(self, x: float, y: float):
        """Move my paddle in the hitting plane (clamped to its range)."""
        self.paddle.x = min(max(x, -config.PADDLE_X_RANGE_M), config.PADDLE_X_RANGE_M)
        self.paddle.y = min(max(y, config.PADDLE_Y_MIN_M), config.PADDLE_Y_MAX_M)

    def ball_normalized(self):
        """Ball position for the LED matrix (top view): x 0..1 left->right,
        y 0..1 from the opponent's end (0) to mine (1), both clamped."""
        x = (self.ball.x + config.TABLE_WIDTH / 2) / config.TABLE_WIDTH
        y = 1.0 - self.ball.z / config.TABLE_LENGTH
        return min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)

    # ------------------------------------------------------------ update
    def update(self, dt: float, now: float, swing: Optional[Swing] = None) -> list:
        """Advance the game by dt seconds. `now` = time.monotonic(); `swing` =
        the latest swing from the swing source (or None). Returns events."""
        dt = min(dt, config.MAX_DT)
        self._now = now
        self.game_time += dt
        events = []
        if swing is not None and swing.arrival == self._used_swing:
            swing = None   # already used for a hit: each swing counts once

        if self.state in (MISS, POINT):
            if not self.holding:
                step_ball(self.ball, dt)   # let the ball fly on / drop
            self.opponent.step(dt, self.game_time)
            self.pause_timer -= dt
            if self.pause_timer <= 0:
                self._serve()
                events.append(EVENT_SERVE)
            return events

        if self.state != PLAYING:
            return events

        self.opponent.step(dt, self.game_time)
        if self.holding:
            # Ball pressed into my paddle: slow it down smoothly (no gravity),
            # sinking at most HOLD_MAX_SINK_M past the hitting plane so it
            # stays on the paddle (on screen too).
            decay = math.exp(-dt / config.HOLD_DECAY_S)
            b = self.ball
            b.vx, b.vy, b.vz = b.vx * decay, b.vy * decay, b.vz * decay
            b.x += b.vx * dt
            b.y += b.vy * dt
            b.z = max(b.z + b.vz * dt, config.MY_HIT_Z - config.HOLD_MAX_SINK_M)
        else:
            z_before = self.ball.z
            if step_ball(self.ball, dt):
                events.append(EVENT_BOUNCE)
                self.last_bounce = (self.ball.x, self.ball.z, self.game_time)
                if self.ball.z < config.TABLE_LENGTH / 2:
                    self.bounced_my_side = True
                else:
                    self.bounced_opp_side = True
            if self.coming_to_me:
                # Check 1: did the ball cross my hitting plane after bouncing on my side?
                if z_before > config.MY_HIT_Z >= self.ball.z and self.bounced_my_side:
                    self._start_contact(now)
                elif self.ball.y < -0.5:   # fell off without a proper bounce
                    events += self._my_miss()
            else:
                if z_before < config.OPP_HIT_Z <= self.ball.z:
                    events += self._opponent_turn()
                elif self.ball.y < -0.5:
                    events += self._opponent_turn()

        if not self.holding:
            return events

        verdict = is_hit(self._contact_ball, self._contact_paddle, self.params["hit_tolerance"],
                         self.contact_time, swing, now)
        if verdict is None:
            return events                     # keep waiting for a late swing
        self.holding = False
        self.contact_time = None
        if verdict:
            self._used_swing = swing.arrival
            self.last_swing_offset = swing.arrival - self._contact_time_used
            self.last_return_factor = return_speed_factor(swing.peak)
            self._my_return(self.last_return_factor)
            self.streak += 1
            events.append(EVENT_HIT)
            if self.streak > self.best_streak:
                self.best_streak = self.streak
                events.append(EVENT_RECORD)
        else:
            b = self.ball
            b.vx, b.vy, b.vz = self._held_velocity   # drop past me
            events += self._my_miss()
        return events

    # ------------------------------------------------------------ rally steps
    def _start_contact(self, now: float):
        self.contact_time = self._contact_time_used = now
        b = self.ball
        self._contact_ball = Ball(b.x, b.y, b.z, b.vx, b.vy, b.vz)
        self._contact_paddle = Paddle(self.paddle.x, self.paddle.y)
        self._held_velocity = (b.vx, b.vy, b.vz)
        self.holding = True

    def _new_shot(self, coming_to_me: bool):
        self.coming_to_me = coming_to_me
        self.bounced_my_side = self.bounced_opp_side = False

    def _serve(self):
        """The opponent serves: from its end, landing somewhere on my side."""
        self.state = PLAYING
        self.holding = False
        self.contact_time = None
        x = random.uniform(-0.4, 0.4)
        self.opponent.x = x
        self.ball = Ball(x, config.SERVE_HEIGHT, config.OPP_HIT_Z)
        self._aim_at_me()

    def _aim_at_me(self):
        """Opponent's shot to a random spot on my side. The ball keeps drifting
        sideways after the bounce, so check where it will reach my hitting
        plane and re-pick the spot if that's outside my paddle's reach: every
        ball the opponent sends must be returnable."""
        self._new_shot(coming_to_me=True)
        start = Ball(self.ball.x, self.ball.y, self.ball.z)
        limit = config.PADDLE_X_RANGE_M - config.PADDLE_RADIUS_M / 2
        for attempt in range(20):
            self.ball = Ball(start.x, start.y, start.z)
            spread = config.LANDING_X_MAX * (1 - attempt / 20)   # narrower each retry
            aim(self.ball, random.uniform(-spread, spread),
                random.uniform(*config.MY_LANDING_Z), self.params["ball_speed"])
            cross = predict_crossing(self.ball, config.MY_HIT_Z)
            if cross is not None and abs(cross) <= limit:
                return

    def _my_return(self, factor: float):
        """Harder swing = faster AND deeper return: the speed is the level's
        ball speed x factor, and the landing depth moves from just past the
        net (soft) to near the opponent's end (hard)."""
        b = self.ball
        b.z = config.MY_HIT_Z   # leave from the hitting plane (out of the cushion)
        frac = (factor - config.RETURN_FACTOR_MIN) / (config.RETURN_FACTOR_MAX - config.RETURN_FACTOR_MIN)
        frac = min(max(frac, 0.0), 1.0)
        target_z = config.OPP_LANDING_Z_SOFT + frac * (config.OPP_LANDING_Z_HARD - config.OPP_LANDING_Z_SOFT)
        target_x = random.uniform(-config.LANDING_X_MAX, config.LANDING_X_MAX)
        self._new_shot(coming_to_me=False)
        aim(b, target_x, target_z, self.params["ball_speed"] * factor)
        self.opponent.watch(b, self.game_time)

    def _opponent_turn(self) -> list:
        """The ball reached the opponent's hitting plane (or fell): return it or miss."""
        b = self.ball
        if (self.bounced_opp_side and b.y > -0.2
                and abs(self.opponent.x - b.x) <= config.OPP_REACH_M):
            b.z = config.OPP_HIT_Z
            b.y = max(b.y, 0.15)
            self._aim_at_me()
            self.opponent.go_home(self.game_time + 0.3)
            return [EVENT_OPP_HIT]
        # Opponent missed: the point is mine, the rally restarts, streak continues.
        self.state = POINT
        self.pause_timer = config.POINT_PAUSE_S
        self.opponent.go_home(self.game_time + 0.4)
        return [EVENT_POINT]

    def _my_miss(self) -> list:
        self.state = MISS
        self.pause_timer = config.MISS_PAUSE_S
        self.streak = 0
        self.holding = False
        self.contact_time = None
        return [EVENT_MISS]
