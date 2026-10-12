"""Game rules: 3D ball physics, the hit rule, the opponent, and the streaks.

No pygame in this file, on purpose: it only does math on numbers, so it can be
tested on its own and doesn't care where the paddle position or the swing come
from (keyboard, camera, IMU), or how things are drawn (view3d.py, draw.py).

World coordinates are meters (see config.py):
    x  across the table (0 = center, + = right)
    y  height above the table surface
    z  along the table (0 = MY end, TABLE_LENGTH = the opponent's end)
Times (`now`, contact and swing times) are time.monotonic() seconds.

THE GOAL: as many volleys (hits of mine) in a row as possible. A rally: the
opponent serves -> the ball arcs over the net, bounces once on my side -> it
reaches my hitting plane ("contact") -> the hit rule decides -> on a hit the
ball arcs back over the net, bounces once on the opponent's side -> the
opponent (a simple AI that never misses) returns it -> ... until I miss.

THE RALLY RAMP: every hit makes the next ball a bit harder (GameState
speed_factor / aim_spread / zone_scale, values in config.py):
  - the ball speed (all shots) grows by the level's speed_ramp per hit, up
    to the level's speed_max (below the level's arc_speed the ball keeps the
    arc of an arc_speed shot and moves in slow motion, so slow balls don't
    fly higher than the paddle can reach: see pace);
  - the opponent aims wider across my side (AIM_SPREAD_*);
  - the hit zone's tolerances shrink with the speed, down to a floor (ZONE_*).
A miss resets the volley count, so everything goes back to the level's
starting values. The level (AprilTag 0/1/2) sets the starting speed and how
fast it ramps.

POWER-UP CARDS (AprilTags 10 and 11, each once per rally: both come back
when a rally ends; use_slowmo(), use_extra_life()):
  - SLOW-MO: while playing, the ball and the opponent move at SLOWMO_SPEED x
    their current speed until SLOWMO_BALLS balls have reached my paddle
    (game time runs slower; the ramp keeps counting, so afterwards the speed
    is where the ramp would be).
  - EXTRA LIFE: a miss pauses the game in the EXTRA_LIFE state with an
    EXTRA_LIFE_COUNTDOWN_S countdown; showing the card in time undoes the miss
    (volley count and ramp continue, new serve). If the time runs out, or the
    card was already used, the miss ends the rally as usual.
"""
import math
import random
from collections import deque
from dataclasses import dataclass
from typing import Optional

import config

# States of the game.
#   WAITING: before a game starts (waiting for Space / an AprilTag)
#   READY:   "3, 2, 1, GO!" before every serve; the ball is served at GO
#   PLAYING: the ball is in play (including a short wait for a late swing)
#   EXTRA_LIFE: I missed with my extra life unused: paused, counting down;
#            the EXTRA LIFE card undoes the miss, else the rally ends
#   GAME_OVER: the rally ended (my miss): final volley count shown, the game
#            waits; playing again (a swing after GAME_OVER_SWING_IGNORE_S, a
#            level tag, or Space) goes to READY with fresh cards. After
#            GAME_OVER_IDLE_S it goes back to WAITING.
# (The opponent never misses, so there is no state for that.)
WAITING = "WAITING"
READY = "READY"
PLAYING = "PLAYING"
EXTRA_LIFE = "EXTRA_LIFE"
GAME_OVER = "GAME_OVER"

# Events returned by GameState.update(), for sounds and MQTT to react to.
EVENT_HIT = "hit"
EVENT_MISS = "miss"               # my miss ended the rally (comes with EVENT_GAME_OVER)
EVENT_GAME_OVER = "game_over"     # GAME_OVER screen: final score in last_rally
EVENT_IDLE = "idle"               # GAME_OVER timed out: back to WAITING
EVENT_RECORD = "record"           # best_streak (the volley record) just went up
EVENT_SERVE = "serve"             # the ball is served (at GO)
EVENT_READY_COUNT = "ready_count" # the serve countdown shows a new number (ready_number)
EVENT_GO = "go"                   # the serve countdown reached GO
EVENT_BEST_RESET = "best_reset"   # best_streak was reset (AprilTag 5)
EVENT_BOUNCE = "bounce"           # ball bounced on the table
EVENT_OPP_HIT = "opp_hit"         # the opponent returned the ball (it always does)
EVENT_SLOWMO = "slowmo"           # SLOW-MO card used
EVENT_LIFE_OFFER = "life_offer"   # I missed with the extra life unused: countdown starts
EVENT_COUNTDOWN = "countdown"     # the countdown shows a new number (countdown_number)
EVENT_LIFE_USED = "life_used"     # EXTRA LIFE card used: the miss is undone
EVENT_CARD_REFUSED = "card_refused"   # a card that is used up / not usable now (card_note says why)


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
    peak_gyro: Optional[float] = None   # peak rotation speed in deg/s, if measured
    failed: Optional[list] = None       # rejected moves only: the check(s) it failed


# Miss reasons (diagnostics: shown on screen and logged; they don't change the rule)
REASON_OUT_OF_ZONE = "OUT_OF_ZONE"      # paddle never on the ball's crossing spot (check 2)
REASON_NO_SWING = "NO_SWING"            # no swing near contact (check 3)
REASON_TOO_EARLY = "SWING_TOO_EARLY"    # a swing arrived, but before the window opened
REASON_TOO_LATE = "SWING_TOO_LATE"      # a swing arrived, but after the window closed
REASON_REJECTED = "SWING_REJECTED"      # a move arrived in time, but the IMU rejected it
                                        # (accel or gyro below the swing thresholds)
REASON_NO_CONTACT = "NO_CONTACT"        # the ball never reached my hitting plane


# =============================================================================
# THE HIT RULE
#
# A return counts as a hit only if all three of these are true:
#
#   1. The ball reaches my paddle. The ball comes over the net, bounces once on
#      my side of the table, and then reaches my hitting plane: an invisible
#      wall just behind my end of the table where my paddle moves. "Contact"
#      is the moment the ball crosses that plane. Nothing is judged before.
#      (To help aim, the screen marks where the ball will cross that plane:
#      a clear target once it bounces on my side, and a faint predicted one
#      from the moment the opponent hits it.)
#
#   2. The paddle was in the right place, just before or at contact. The
#      camera tracks the player's hand, which moves the paddle left/right and
#      up/down in the hitting plane. Left/right and up/down are checked
#      SEPARATELY, so the hit zone is a box around the paddle's center:
#        left/right: |ball x - paddle x| <= paddle radius + ball radius + x tolerance
#        up/down:    |ball y - paddle y| <= paddle radius + ball radius + y tolerance
#      The tolerances depend on the level (config.LEVELS, hit_tolerance):
#      Beginner checks only left/right (height is ignored) with a generous
#      tolerance; Club checks both loosely; Pro checks both tightly. With the
#      camera paddle they are wider than with arrow keys, because tracking a
#      hand is noisier. As a rally speeds up (every hit makes the ball faster,
#      see THE RALLY RAMP at the top), the tolerances shrink with the speed:
#      tolerance x max(ZONE_MIN_SCALE, 1 - ZONE_SHRINK_PER_SPEED x (speed
#      factor - 1)), so 80% of the level's at x1.4 speed, never below 60%.
#      They are back to the level's full values after a miss. The paddle
#      radius itself never shrinks. It is enough for this to be true at ANY moment from
#      PADDLE_LOOKBACK_S (0.2 s) before contact up to contact: swinging the
#      real paddle moves the tracked hand right at contact, so the position the
#      player held just before the swing must count. If the paddle was never
#      there in that time, it's a miss right away. Moving the paddle there
#      after contact doesn't help.
#
#   3. The player really swung, at the right time. The accelerometer and
#      gyroscope on the real paddle send a "swing" message (the UNO Q only
#      sends one for a move that is both hard enough and turning fast enough).
#      The swing is judged by when it ARRIVES on the laptop, in a timing
#      window around contact: from "before" seconds before contact to "after"
#      seconds after it. Which window is used is decided at contact:
#        - SWING_WINDOW_FAST (0.35 s before to 0.35 s after) when swings come
#          through the board's own MQTT broker on the local WiFi (they arrive
#          about 20 ms after the swing), or from Space / --no-imu;
#        - SWING_WINDOW_SLOW (0.15 s before to 0.6 s after) when the local
#          broker is down and swings come over the internet (broker.hivemq.com,
#          0.1 to 0.5 s late or worse), so the window is shifted late.
#      Each swing can only be used for one hit.
#
# Because a swing can arrive after contact, the decision may have to wait.
# While it waits, the ball presses into the paddle and slows to a stop (it
# looks like the paddle cushioning the ball). Then:
#   - a swing arrives inside the window  -> HIT: the ball flies back over the
#     net, faster and deeper the harder the swing was;
#   - the window closes with no swing    -> MISS: the ball drops past me.
#
# The opponent never misses, so a rally only ends with a miss of mine; the
# score is how many hits in a row I made (the volley count).
#
# The power-up cards don't change these three checks:
#   - SLOW-MO only slows the ball (game time runs at SLOWMO_SPEED for the
#     next SLOWMO_BALLS balls). Contact, the paddle look-back and the swing
#     timing window are still measured in real time, so the same swing
#     timing works; the slower ball just gives more time to get there.
#   - EXTRA LIFE comes after the rule: the miss still happens (and is
#     logged), but while the card is unused the game pauses for
#     EXTRA_LIFE_COUNTDOWN_S; showing the card in time undoes the miss, so the
#     volley count goes on. Otherwise the miss ends the rally.
#
# Why this is fair: a swing alone never scores (the paddle must have been on
# the ball's path at contact time), and the paddle alone never scores (there
# must be a real swing close to contact). An occasional false swing from the
# sensor only matters if it lands in that short window while the paddle is
# already on the ball.
# =============================================================================
def zone_reach(tolerance) -> tuple:
    """(left/right, up/down) half-sizes of the hit zone in m, from a level's
    (x, y) tolerance; up/down is None when height isn't checked."""
    base = config.PADDLE_RADIUS_M + config.BALL_RADIUS_M
    tol_x, tol_y = tolerance
    return base + tol_x, (None if tol_y is None else base + tol_y)


def zone_miss(ball_x: float, ball_y: float, paddle: "Paddle", reach: tuple) -> tuple:
    """How far the paddle's center was OUTSIDE the zone, per axis, in m:
    (off_x, off_y), 0 = inside on that axis; off_y is None if height isn't
    checked. Both 0 (or off_y None) = the paddle is on the ball."""
    off_x = max(0.0, abs(ball_x - paddle.x) - reach[0])
    off_y = None if reach[1] is None else max(0.0, abs(ball_y - paddle.y) - reach[1])
    return off_x, off_y


def in_zone(ball_x: float, ball_y: float, paddle: "Paddle", reach: tuple) -> bool:
    off_x, off_y = zone_miss(ball_x, ball_y, paddle, reach)
    return off_x == 0 and not off_y


def is_hit(ball: Ball, paddles: list, reach: tuple, window: tuple, contact_time: Optional[float],
           swing: Optional[Swing], now: float) -> Optional[bool]:
    """Apply the hit rule above.

    ball: where it crossed my hitting plane (at contact_time).
    paddles: the paddle's positions from PADDLE_LOOKBACK_S before contact up
             to contact (one per frame).
    reach: the hit zone's half-sizes (zone_reach() of the level's tolerance).
    window: (before, after) of the swing timing window, in s.
    swing: the latest unused swing, or None.
    Returns True (hit), False (miss), or None (not decided yet: keep waiting).
    """
    # 1. The ball hasn't reached my hitting plane yet: nothing to decide.
    if contact_time is None:
        return None

    # 2. Paddle on the ball's crossing spot at some moment in the look-back.
    if not any(in_zone(ball.x, ball.y, p, reach) for p in paddles):
        return False

    # 3. A swing that arrived inside the timing window around contact.
    opens = contact_time - window[0]
    closes = contact_time + window[1]
    if swing is not None and opens <= swing.arrival <= closes:
        return True
    if now > closes:
        return False   # window closed without a swing
    return None        # window still open: wait


def off_text(off_x: float, dx: float, off_y: Optional[float], dy: float) -> str:
    """e.g. "paddle 12 cm too far LEFT, 5 cm too LOW" (dx, dy = paddle - ball)."""
    parts = []
    if off_x > 0:
        parts.append(f"{off_x * 100:.0f} cm too far {'RIGHT' if dx > 0 else 'LEFT'}")
    if off_y:
        parts.append(f"{off_y * 100:.0f} cm too {'HIGH' if dy > 0 else 'LOW'}")
    return "paddle " + ", ".join(parts) if parts else "paddle on the ball"


def rejected_text(rej: Swing, contact: float) -> str:
    """e.g. "SWING REJECTED (accel 1.8 g < 2.2)  +240 ms" from the IMU's failed checks."""
    parts = []
    for check in rej.failed or []:          # e.g. "accel < 2.2 g", "gyro < 500 dps"
        limit = check.split("<")[-1].strip().split()[0] if "<" in check else "?"
        if check.startswith("accel") and rej.peak is not None:
            parts.append(f"accel {rej.peak:.1f} g < {limit}")
        elif check.startswith("gyro") and rej.peak_gyro is not None:
            parts.append(f"gyro {rej.peak_gyro:.0f} dps < {limit}")
        else:
            parts.append(check)
    return f"SWING REJECTED ({', '.join(parts) or 'failed check'})  {(rej.arrival - contact) * 1000:+.0f} ms"


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
    """A simple AI at the far end that never misses. It watches my return,
    waits its reaction time, then slides its paddle toward the spot where the
    ball will cross its hitting plane. It moves at its usual max_speed, or
    faster when that wouldn't get it there in time, so it always arrives a
    little before the ball (it slides, it never jumps). Then it returns the
    ball (GameState._opponent_turn)."""

    ARRIVE_EARLY_S = 0.08   # aims to be in place this long before the ball

    def __init__(self):
        self.x = 0.0
        self.target_x = 0.0
        self.react_at = 0.0     # game time it starts moving
        self.speed = 0.0        # m/s across the table for the current move
        self.params = config.LEVELS[config.DEFAULT_LEVEL]["opponent"]

    def watch(self, ball: Ball, game_time: float):
        """Called when I return the ball: where and when will it cross?"""
        cross = predict_crossing(ball, config.OPP_HIT_Z)
        self.target_x = cross if cross is not None else ball.x
        self.react_at = game_time + self.params["reaction_s"]
        # The ball's speed along the table doesn't change (no drag; a bounce
        # only flips the vertical speed), so the time to arrive is simple.
        flight = (config.OPP_HIT_Z - ball.z) / ball.vz if ball.vz > 0 else 1.0
        time_to_move = max(flight - self.params["reaction_s"] - self.ARRIVE_EARLY_S, 0.05)
        self.speed = max(self.params["max_speed"], abs(self.target_x - self.x) / time_to_move)

    def step(self, dt: float, game_time: float):
        if game_time < self.react_at:
            return
        step = self.speed * dt
        self.x += max(-step, min(step, self.target_x - self.x))

    def go_home(self, game_time: float):
        self.target_x, self.react_at, self.speed = 0.0, game_time, self.params["max_speed"]


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


def predict_crossing(ball: Ball, plane_z: float, dt: Optional[float] = None) -> Optional[float]:
    """Simulate ahead: the ball's x when it crosses z = plane_z (or None)."""
    spot = predict_crossing_xy(ball, plane_z, dt)
    return None if spot is None else spot[0]


def predict_crossing_xy(ball: Ball, plane_z: float, dt: Optional[float] = None) -> Optional[tuple]:
    """Simulate ahead (same physics, bounces included): (x, y) where the ball
    crosses z = plane_z, or None if it never does."""
    dt = dt or config.PHYSICS_DT
    b = Ball(ball.x, ball.y, ball.z, ball.vx, ball.vy, ball.vz)
    for _ in range(int(5 / dt)):
        bx, by, before = b.x, b.y, b.z
        step_ball(b, dt)
        if (before - plane_z) * (b.z - plane_z) <= 0 and before != b.z:
            f = (plane_z - before) / (b.z - before)   # interpolate within the step
            return bx + f * (b.x - bx), by + f * (b.y - by)
        if b.y < -1.0:
            return None
    return None


class GameState:
    def __init__(self):
        self.state = WAITING
        self.level = config.DEFAULT_LEVEL
        self.streak = 0          # my consecutive successful returns
        self.best_streak = 0     # record (published to MQTT when it changes)
        self.last_rally = 0      # final volley count of the rally that just ended (GAME_OVER)
        self.new_high_score = False   # did that rally set a new record (>= MIN_RECORD_FOR_HIGH_SCORE)?
        self.game_over_at = None      # time (now) the GAME_OVER screen started
        self._best_before_rally = 0   # the record when the rally started
        self.paddle = Paddle(0.0, (config.PADDLE_Y_MIN_M + config.PADDLE_Y_MAX_M) / 2)
        self.ball = Ball(0.0, config.SERVE_HEIGHT, config.OPP_HIT_Z)
        self.opponent = Opponent()
        self.game_time = 0.0     # seconds of play (drives the opponent)
        self.camera_mode = False   # camera paddle: use the level's hit_tolerance_camera
        # Swing timing window (before, after) for the NEXT contact; game.py sets
        # it every frame from how swings are arriving (local broker or not).
        self.swing_window = config.SWING_WINDOW_FAST
        self._window = self.swing_window   # the window of the current contact
        # Where the ball will cross my hitting plane: (x, y), or None. Set when
        # the opponent hits (predicted) and again when it bounces on my side
        # (target_locked = True).
        self.target = None
        self.target_locked = False
        self._paddle_history = deque()   # (time, Paddle) for the hit rule's look-back

        # Rally bookkeeping
        self.coming_to_me = True      # ball heading my way (else to the opponent)
        self.bounced_my_side = False
        self.bounced_opp_side = False
        self.last_bounce = None       # (x, z, game_time) of the last bounce, for drawing

        # Contact / waiting for a swing (see the hit rule)
        self.holding = False
        self.contact_time = None
        self._contact_ball = None
        self._contact_paddles = []
        self._held_velocity = (0.0, 0.0, 0.0)
        self._used_swing = None
        self._now = None
        # For the screen: the last hit's swing timing and return speed
        self.last_swing_offset = None
        self.last_return_factor = None

        # Diagnostics: why the last miss happened, and one record per ball
        self.tracking_ok = True       # set by game.py: is the hand tracked (else "lost")?
        self.tracking = "keys"        # set by game.py: wrist / hand / elbow / hold / lost / keys
        self.miss_text = None         # e.g. "OUT OF ZONE  12 cm off" (shown during MISS)
        self.ball_results = []        # finished balls, drained by game.py (CSV log)
        self._contact_info = None     # distances etc. measured at contact
        self._pending_miss = None     # miss record waiting to see if a late swing turns up
        self._rejected = None         # latest rejected move (from update())

        # Power-up cards (both reset by start())
        self.slowmo_used = False
        self.slowmo_left = 0          # balls still to reach my paddle in slow motion
        self.extra_life_used = False
        self.countdown_left = 0.0     # s left in the EXTRA_LIFE countdown
        self._count_shown = 0
        self.card_note = None         # (text, time) e.g. "SLOW-MO card already used"
        self.card_flash = None        # (letter, time) for the LED matrix: "S" / "L"
        # Serve countdown ("3, 2, 1, GO!")
        self.ready_left = 0.0         # s left before GO
        self._ready_shown = 0
        self.go_at = None             # time (now) of the last GO, for the screen / LED matrix
        self._shot_pace = 1.0         # time scale of the shot in the air (see pace)
        self._phys_carry = 0.0        # game time not yet simulated (< PHYSICS_DT)
        self._miss_record = None      # CSV row of the ball an extra life could rescue

    # ------------------------------------------------------------ controls
    @property
    def params(self) -> dict:
        return config.LEVELS[self.level]

    # ---- the rally ramp: everything below follows the volley count (streak)
    @property
    def speed_factor(self) -> float:
        """Current ball speed / the level's start_speed: +speed_ramp per hit
        this rally, capped at speed_max. 1.0 again after a miss."""
        p = self.params
        return min(1.0 + p["speed_ramp"] * self.streak, p["speed_max"] / p["start_speed"])

    @property
    def max_speed_factor(self) -> float:
        return self.params["speed_max"] / self.params["start_speed"]

    @property
    def ball_speed(self) -> float:
        """m/s along the table for the next shot (before my swing's factor).
        The one place the ramped speed comes from (slow-mo scales it)."""
        return self.params["start_speed"] * self.speed_factor

    @property
    def pace(self) -> float:
        """Below the level's arc_speed a ball would need a much higher arc to
        clear the net (and would cross my plane above the paddle's reach). So
        a shot is aimed as if it were at least arc_speed fast, and played back
        at pace = ball_speed / arc_speed (< 1): same arc, slower ball. At or
        above arc_speed, pace = 1."""
        return min(1.0, self.ball_speed / self.params["arc_speed"])

    @property
    def aim_spread(self) -> float:
        """The opponent aims within +-this (m) across my side: wider every hit."""
        return min(config.AIM_SPREAD_START_M + config.AIM_SPREAD_PER_HIT_M * self.streak,
                   config.AIM_SPREAD_MAX_M)

    @property
    def zone_scale(self) -> float:
        """How much of the level's hit tolerance is left at the current speed."""
        return max(config.ZONE_MIN_SCALE,
                   1.0 - config.ZONE_SHRINK_PER_SPEED * (self.speed_factor - 1.0))

    @property
    def hit_tolerance(self) -> tuple:
        """(left/right, up/down) tolerance in m for this level and paddle
        input, shrunk for the current speed (zone_scale)."""
        key = "hit_tolerance_camera" if self.camera_mode else "hit_tolerance"
        tol_x, tol_y = self.params[key]
        k = self.zone_scale
        return tol_x * k, (None if tol_y is None else tol_y * k)

    @property
    def zone(self) -> tuple:
        """Hit zone half-sizes (left/right, up/down or None) in m (see the hit rule)."""
        return zone_reach(self.hit_tolerance)

    @property
    def hold_progress(self) -> float:
        """0..1: how much of the post-contact swing window has passed (for drawing)."""
        if not self.holding or self.contact_time is None or self._now is None:
            return 0.0
        return min(1.0, (self._now - self.contact_time) / self._window[1])

    def set_level(self, level: int):
        """Pick a level (only on the WAITING or GAME_OVER screen)."""
        if self.state in (WAITING, GAME_OVER) and level in config.LEVELS:
            self.level = level
            self.opponent.params = config.LEVELS[level]["opponent"]

    @property
    def can_play_again(self) -> bool:
        """On the GAME_OVER screen, after GAME_OVER_SWING_IGNORE_S (so the last
        swing's follow-through, or a Space pressed as a swing, doesn't restart
        the game at once)."""
        return (self.state == GAME_OVER and self.game_over_at is not None and self._now is not None
                and self._now - self.game_over_at >= config.GAME_OVER_SWING_IGNORE_S)

    def start(self) -> list:
        """WAITING or GAME_OVER -> READY (the serve countdown). Returns events.
        A new rally: volley count 0, both cards available."""
        if self.state == GAME_OVER and not self.can_play_again:
            return []
        if self.state not in (WAITING, GAME_OVER):
            return []
        self.streak = 0
        self.miss_text = None
        self._best_before_rally = self.best_streak
        self.slowmo_used = self.extra_life_used = False
        self.slowmo_left = 0
        self.opponent.params = self.params["opponent"]
        return self._serve()

    @property
    def ready_number(self) -> int:
        """3, 2, 1 during the serve countdown (0 otherwise)."""
        if self.state != READY:
            return 0
        return math.ceil(self.ready_left / config.SERVE_COUNT_STEP_S - 1e-9)

    # ------------------------------------------------------------ power-up cards
    @property
    def countdown_number(self) -> int:
        """EXTRA_LIFE_COUNTDOWN_S down to 1 during the EXTRA_LIFE countdown (0 otherwise)."""
        return math.ceil(self.countdown_left - 1e-9) if self.state == EXTRA_LIFE else 0

    @property
    def time_scale(self) -> float:
        """How fast the ball and the opponent move: the shot's pace (< 1 for
        balls slower than arc_speed), times SLOWMO_SPEED in slow-mo."""
        return self._shot_pace * (config.SLOWMO_SPEED if self.slowmo_left > 0 else 1.0)

    def use_slowmo(self) -> list:
        """SLOW-MO card shown (held POWERUP_HOLD_S). Only while PLAYING, once per rally."""
        if self.slowmo_used:
            return self._refuse("SLOW-MO card already used")
        if self.state not in (PLAYING, READY):
            return self._refuse("SLOW-MO works during a rally")
        self.slowmo_used = True
        self.slowmo_left = config.SLOWMO_BALLS
        self.card_flash = ("S", self._now or 0.0)
        return [EVENT_SLOWMO]

    def use_extra_life(self) -> list:
        """EXTRA LIFE card shown. Only during the countdown after a miss, once
        per rally: undoes the miss (volley count and ramp continue), new serve."""
        if self.extra_life_used:
            return self._refuse("EXTRA LIFE card already used")
        if self.state != EXTRA_LIFE:
            return self._refuse("EXTRA LIFE: show it when you miss")
        self.extra_life_used = True
        self.countdown_left = 0.0
        self.card_flash = ("L", self._now or 0.0)
        # Log it on the ball it saved
        if self._contact_info is not None:
            self._contact_info["powerup"] = "+".join(
                x for x in (self._contact_info.get("powerup"), "extra_life") if x)
        if self._miss_record is not None:
            self._miss_record["powerup"] = (self._contact_info or {}).get("powerup", "extra_life")
        self.miss_text = None
        return [EVENT_LIFE_USED] + self._serve()

    def _refuse(self, text: str) -> list:
        self.card_note = (text, self._now or 0.0)
        return [EVENT_CARD_REFUSED]

    def take_results(self) -> list:
        """Finished balls for the CSV log. Held back during the extra-life
        countdown, so the missed ball's row can still say if the card saved it."""
        if self.state == EXTRA_LIFE:
            return []
        out, self.ball_results = self.ball_results, []
        return out

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
    def update(self, dt: float, now: float, swing: Optional[Swing] = None,
               rejected: Optional[Swing] = None) -> list:
        """Advance the game by dt seconds. `now` = time.monotonic(); `swing` =
        the latest swing from the swing source (or None); `rejected` = the
        latest move the IMU rejected (diagnostics only). Returns events."""
        dt = min(dt, config.MAX_DT)
        self._now = now
        # Slow-mo: the ball and the opponent run on game time, which runs at
        # SLOWMO_SPEED while it's active. (The swing window and the paddle's
        # look-back use real time: the player doesn't slow down.)
        game_dt = dt * self.time_scale
        self.game_time += game_dt
        events = []
        if swing is not None and swing.arrival == self._used_swing:
            swing = None   # already used for a hit: each swing counts once
        # Remember where the paddle was (for the hit rule's look-back)
        self._paddle_history.append((now, Paddle(self.paddle.x, self.paddle.y)))
        while self._paddle_history and self._paddle_history[0][0] < now - config.PADDLE_LOOKBACK_S - 0.05:
            self._paddle_history.popleft()

        self._rejected = rejected
        self._watch_for_late_swing(swing, now)

        if self.state == EXTRA_LIFE:
            # Paused: only the countdown runs (use_extra_life() ends it early)
            self.countdown_left -= dt
            if self.countdown_left <= 0:
                self.countdown_left = 0.0
                self.state = PLAYING          # (so _my_miss ends the rally normally)
                events += self._my_miss()
            elif 0 < self.countdown_number < self._count_shown:   # 4, 3, 2, 1 (never 0)
                self._count_shown = self.countdown_number
                events.append(EVENT_COUNTDOWN)
            return events

        if self.state == READY:
            # "3, 2, 1, GO!": the ball waits at the opponent's end; served at GO
            self.ready_left -= dt
            if self.ready_left <= 0:
                events += self._launch_serve()
            elif 0 < self.ready_number < self._ready_shown:
                self._ready_shown = self.ready_number
                events.append(EVENT_READY_COUNT)
            return events

        if self.state == GAME_OVER:
            if self.ball.y > -1.0:
                step_ball(self.ball, dt)   # let the missed ball drop away
            self.opponent.step(dt, self.game_time)
            if now - self.game_over_at >= config.GAME_OVER_IDLE_S:
                self.state = WAITING       # nobody played again: back to the start screen
                events.append(EVENT_IDLE)
            return events

        if self.state != PLAYING:
            return events

        self.opponent.step(game_dt, self.game_time)
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
            # Physics in fixed steps of exactly PHYSICS_DT of game time (the
            # remainder carries over to the next frame), so bounces and the
            # crossing spot don't depend on the frame rate or the pace, and
            # match the target prediction (predict_crossing_xy, same step).
            self._phys_carry += game_dt
            n = int(self._phys_carry / config.PHYSICS_DT + 1e-9)
            self._phys_carry -= n * config.PHYSICS_DT
            for _ in range(n):
                b = self.ball
                x_before, y_before, z_before = b.x, b.y, b.z
                if step_ball(b, config.PHYSICS_DT):
                    events.append(EVENT_BOUNCE)
                    self.last_bounce = (b.x, b.z, self.game_time)
                    if b.z < config.TABLE_LENGTH / 2:
                        self.bounced_my_side = True
                        if self.coming_to_me:   # lock the target: the ball is about to arrive
                            self.target = predict_crossing_xy(b, config.MY_HIT_Z)
                            self.target_locked = self.target is not None
                    else:
                        self.bounced_opp_side = True
                if self.coming_to_me:
                    # Check 1: did the ball cross my hitting plane after bouncing on my side?
                    if z_before > config.MY_HIT_Z >= b.z and self.bounced_my_side:
                        f = (config.MY_HIT_Z - z_before) / (b.z - z_before)   # exact crossing spot
                        self._start_contact(now, x_before + f * (b.x - x_before),
                                            y_before + f * (b.y - y_before))
                        break
                    if b.y < -0.5:   # fell off without a proper bounce
                        self._contact_info = None
                        self.miss_text = "NO CONTACT"
                        self._miss_record = self._record("miss", REASON_NO_CONTACT, None)
                        self.ball_results.append(self._miss_record)
                        self.slowmo_left = max(0, self.slowmo_left - 1)
                        events += self._miss_or_offer_life()
                        break
                elif z_before < config.OPP_HIT_Z <= b.z or b.y < -0.5:
                    events += self._opponent_turn()
                    break

        if not self.holding:
            return events

        verdict = is_hit(self._contact_ball, self._contact_paddles, self.zone, self._window,
                         self.contact_time, swing, now)
        if verdict is None:
            return events                     # keep waiting for a late swing
        self.holding = False
        self.slowmo_left = max(0, self.slowmo_left - 1)   # one more ball played in slow-mo
        self.contact_time = None
        self.target = None
        if verdict:
            self._used_swing = swing.arrival
            self.last_swing_offset = swing.arrival - self._contact_time_used
            self.last_return_factor = return_speed_factor(swing.peak)
            self.ball_results.append(self._record("hit", None, swing))
            self._my_return(self.last_return_factor)
            self.streak += 1
            events.append(EVENT_HIT)
            if self.streak > self.best_streak:
                self.best_streak = self.streak
                events.append(EVENT_RECORD)
        else:
            b = self.ball
            b.vx, b.vy, b.vz = self._held_velocity   # drop past me
            self._explain_miss(swing, now)
            events += self._miss_or_offer_life()
        return events

    # ------------------------------------------------------------ rally steps
    def _start_contact(self, now: float, x: float, y: float):
        """The ball crossed my hitting plane at (x, y) during this frame."""
        self.contact_time = self._contact_time_used = now
        b = self.ball
        self._contact_ball = Ball(x, y, config.MY_HIT_Z, b.vx, b.vy, b.vz)
        b = self._contact_ball
        self._window = self.swing_window   # the timing window is fixed at contact
        # Paddle positions from PADDLE_LOOKBACK_S before contact up to now (contact)
        self._contact_paddles = [p for t, p in self._paddle_history
                                 if t >= now - config.PADDLE_LOOKBACK_S]
        # Diagnostics: the paddle position in the look-back that came closest
        # to the zone, split into left/right and up/down.
        reach = self.zone

        def outside(p):
            ox, oy = zone_miss(b.x, b.y, p, reach)
            return math.hypot(ox, oy or 0.0)
        best = min(self._contact_paddles, key=outside)
        off_x, off_y = zone_miss(b.x, b.y, best, reach)
        tol_x, tol_y = self.hit_tolerance
        self._contact_info = {"time": now, "dx_m": best.x - b.x, "dy_m": best.y - b.y,
                              "off_x_m": off_x, "off_y_m": off_y, "tol_x_m": tol_x, "tol_y_m": tol_y,
                              "tracking_ok": self.tracking_ok, "tracking": self.tracking,
                              "window": self._window, "volley": self.streak,
                              "speed_x": self.speed_factor,
                              "powerup": "slowmo" if self.slowmo_left > 0 else ""}
        self._held_velocity = (b.vx, b.vy, b.vz)
        self.holding = True

    def _new_shot(self, coming_to_me: bool):
        self.coming_to_me = coming_to_me
        self.bounced_my_side = self.bounced_opp_side = False

    def _serve(self) -> list:
        """A new ball: first the "3, 2, 1, GO!" countdown (state READY), with
        the ball held at the opponent's end; _launch_serve() serves it at GO."""
        self.state = READY
        self.holding = False
        self.contact_time = None
        self.target = None
        self.ready_left = config.SERVE_COUNT_FROM * config.SERVE_COUNT_STEP_S
        self._ready_shown = self.ready_number
        x = random.uniform(-0.4, 0.4)
        self.opponent.x = self.opponent.target_x = x
        self.ball = Ball(x, config.SERVE_HEIGHT, config.OPP_HIT_Z)
        return [EVENT_READY_COUNT]

    def _launch_serve(self) -> list:
        """GO: the opponent serves, landing somewhere on my side."""
        self.state = PLAYING
        self.go_at = self._now
        self._aim_at_me()
        return [EVENT_GO, EVENT_SERVE]

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
            spread = self.aim_spread * (1 - attempt / 20)   # wider as the rally grows; narrower each retry
            self._shot_pace = self.pace     # slower than arc_speed: same arc, slow motion
            aim(self.ball, random.uniform(-spread, spread),
                random.uniform(*config.MY_LANDING_Z), self.ball_speed / self._shot_pace)
            cross = predict_crossing(self.ball, config.MY_HIT_Z)
            if cross is not None and abs(cross) <= limit:
                break
        # Predicted crossing spot, shown faintly until the ball bounces on my side
        self.target = predict_crossing_xy(self.ball, config.MY_HIT_Z) if config.TARGET_EARLY else None
        self.target_locked = False

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
        self.target = None
        self._shot_pace = self.pace
        aim(b, target_x, target_z, self.ball_speed * factor / self._shot_pace)
        self.opponent.watch(b, self.game_time)

    def _opponent_turn(self) -> list:
        """The ball reached the opponent's hitting plane: it always returns it
        (at the current, ramped-up speed and aim spread)."""
        b = self.ball
        b.z = config.OPP_HIT_Z
        b.y = max(b.y, 0.15)
        self._aim_at_me()
        self.opponent.go_home(self.game_time + 0.3)
        return [EVENT_OPP_HIT]

    # ------------------------------------------------------------ diagnostics
    def _explain_miss(self, swing: Optional[Swing], now: float):
        """Why did the hit rule say miss? Sets miss_text; logs the ball (or, for
        swing-timing misses, waits LATE_SWING_WATCH_S in case a late swing shows up)."""
        info = self._contact_info
        contact = info["time"]
        if info["off_x_m"] > 0 or info["off_y_m"]:
            # Second line (smaller on screen): which way, and how far, per axis
            detail = off_text(info["off_x_m"], info["dx_m"], info["off_y_m"], info["dy_m"])
            if not info["tracking_ok"]:
                detail += "  (hand lost)"
            self.miss_text = f"OUT OF ZONE\n{detail}"
            self._miss_record = self._record("miss", REASON_OUT_OF_ZONE, None)
            self.ball_results.append(self._miss_record)
            return
        opens = contact - info["window"][0]
        closes = contact + info["window"][1]
        rej = self._rejected
        if rej is not None and opens <= rej.arrival <= closes:
            # A move came in time but didn't pass the IMU's swing checks
            self.miss_text = rejected_text(rej, contact)
            record = self._record("miss", REASON_REJECTED, rej)
        elif (swing is not None and swing.arrival < opens
                and contact - swing.arrival <= config.EARLY_SWING_LOOK_S):
            offset_ms = (swing.arrival - contact) * 1000
            self.miss_text = f"SWING TOO EARLY  {offset_ms:+.0f} ms"
            record = self._record("miss", REASON_TOO_EARLY, swing)
        else:
            self.miss_text = "NO SWING"
            record = self._record("miss", REASON_NO_SWING, None)
        # A late swing may still arrive (network delay): keep watching.
        self._pending_miss = (record, contact + config.LATE_SWING_WATCH_S)
        self._miss_record = record

    def _watch_for_late_swing(self, swing: Optional[Swing], now: float):
        """After a timing miss: a swing arriving after the window closed relabels
        it SWING TOO LATE. The record is logged when the watch ends."""
        if self._pending_miss is None:
            return
        record, watch_until = self._pending_miss
        contact = self._contact_info["time"]
        closes = contact + self._contact_info["window"][1]
        if (swing is not None and swing.arrival > closes and swing.arrival <= watch_until
                and record["reason"] != REASON_TOO_LATE):
            offset_ms = (swing.arrival - contact) * 1000
            self.miss_text = f"SWING TOO LATE  {offset_ms:+.0f} ms"
            record.update(self._record("miss", REASON_TOO_LATE, swing))
        rej = self._rejected
        if (rej is not None and record["reason"] == REASON_NO_SWING
                and closes < rej.arrival <= watch_until):
            self.miss_text = rejected_text(rej, contact) + " (late)"
            record.update(self._record("miss", REASON_REJECTED, rej))
        if now >= watch_until or self.state == PLAYING:
            self.ball_results.append(record)
            self._pending_miss = None

    def _record(self, result: str, reason: Optional[str], swing: Optional[Swing]) -> dict:
        """One row for the ball log (see game.py / ball_log.py)."""
        info = self._contact_info or {}
        contact = info.get("time")
        cm = lambda key: None if info.get(key) is None else round(info[key] * 100, 1)
        window = info.get("window")
        return {
            "level": self.level,
            # Hits so far in the rally when this ball came, and the ramp's speed
            # factor then (taken at contact: a late relabel runs after the reset)
            "volley": info.get("volley", self.streak),
            "speed_x": round(info.get("speed_x", self.speed_factor), 2),
            "result": result,
            "reason": reason or "",
            "dx_cm": cm("dx_m"),          # paddle - ball: + = paddle to the right
            "dy_cm": cm("dy_m"),          # + = paddle higher than the ball
            "off_x_cm": cm("off_x_m"),    # how far outside the zone (0 = inside)
            "off_y_cm": cm("off_y_m"),    # empty = height not checked (Beginner)
            "tol_x_cm": cm("tol_x_m"),
            "tol_y_cm": cm("tol_y_m"),
            "swing_offset_ms": (None if swing is None or contact is None
                                else round((swing.arrival - contact) * 1000)),
            "swing_window_ms": (None if window is None
                                else f"-{window[0] * 1000:.0f}/+{window[1] * 1000:.0f}"),
            "swing_peak_g": None if swing is None else swing.peak,
            "swing_peak_dps": None if swing is None else swing.peak_gyro,
            "tracking": info.get("tracking"),
            "hand_tracked": info.get("tracking_ok"),
            "rejected_why": "" if swing is None or not swing.failed else "; ".join(swing.failed),
            # Power-up on this ball: "slowmo" (played in slow motion), "extra_life"
            # (a miss the card undid), or both
            "powerup": info.get("powerup", ""),
        }

    def flush_results(self):
        """Log a still-pending miss (e.g. when quitting)."""
        if self._pending_miss is not None:
            self.ball_results.append(self._pending_miss[0])
            self._pending_miss = None

    def _miss_or_offer_life(self) -> list:
        """I missed. With the extra life unused: pause and count down (the
        EXTRA LIFE card can undo the miss). Otherwise the rally ends now."""
        if self.extra_life_used:
            return self._my_miss()
        self.state = EXTRA_LIFE
        self.countdown_left = config.EXTRA_LIFE_COUNTDOWN_S
        self._count_shown = self.countdown_number
        self.holding = False
        self.contact_time = None
        self.target = None
        return [EVENT_LIFE_OFFER, EVENT_COUNTDOWN]

    def _my_miss(self) -> list:
        """The rally is over (a miss, after any extra-life countdown): GAME OVER."""
        self.slowmo_left = 0      # slow-mo ends with the rally
        # Fresh cards for the next rally (whether or not they were used, and
        # whether a timed-out extra life counts as spent doesn't matter)
        self.slowmo_used = self.extra_life_used = False
        self.state = GAME_OVER
        self.game_over_at = self._now if self._now is not None else 0.0
        self.last_rally = self.streak
        # A new record: beat the record of when the rally started, still is the
        # record (a bigger one may have loaded from MQTT meanwhile), long enough
        self.new_high_score = (self.last_rally > self._best_before_rally
                               and self.last_rally >= self.best_streak
                               and self.last_rally >= config.MIN_RECORD_FOR_HIGH_SCORE)
        self.streak = 0          # the volley count, and with it the ramp, starts over
        self.target = None
        self.holding = False
        self.contact_time = None
        return [EVENT_MISS, EVENT_GAME_OVER]
