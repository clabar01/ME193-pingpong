"""Drawing only: turns a GameState into pixels. No game rules in here.

The table scene is drawn in first person with view3d.project(); the screens
and the HUD are flat on top. Kept simple for a first-time player:

  playing      the volley count (big, top center), BEST (small, top right),
               icons for the power-up cards still available, the target box
               and the paddle, a small camera view in the bottom-left corner
  messages     ONE at a time (game.py keeps a single banner): GO!, HIGH
               SCORE!, SLOW-MO!, EXTRA LIFE!, ALREADY USED and the miss word
               ("TOO LATE", "MISSED", "SWING!"); big, short, quick to fade
  screens      start (how to play in 3 steps), calibration (big arrow),
               3-2-1, EXTRA LIFE countdown, GAME OVER

Colors mean one thing each: GOOD (green: in position, hits, GO, high score),
BAD (red: misses, game over), POWER (blue: the power-up cards).

Everything for tuning and diagnosis (game state, level and checks, speed,
MQTT and brokers, swing meter with delays, tracking source, sound trace,
miss details, calibration sentences, card notes) is in the debug overlay:
D toggles it, `game.py --debug` starts with it on.
"""
import math

import pygame

import config
import game_state as gs
import view3d

BACKGROUND = (18, 24, 38)
FLOOR = (26, 32, 46)
WALL = (70, 80, 100)
TABLE = (28, 78, 140)
TABLE_EDGE = (18, 50, 92)
LINE = (235, 240, 245)
NET = (230, 235, 240, 70)
POST = (60, 64, 72)
BALL = (255, 250, 240)
BALL_EDGE = (200, 190, 170)
PADDLE = (240, 120, 60)
HANDLE = (120, 80, 50)
OPPONENT = (215, 60, 70)
HIT_RING = (255, 210, 160)
TEXT = (235, 235, 235)
DIM_TEXT = (150, 160, 180)
FAINT = (85, 92, 110)

# The three meaning colors
GOOD = (110, 225, 140)    # in position, hits, GO, high score
BAD = (255, 95, 95)       # misses, game over
POWER = (130, 170, 255)   # power-up cards (slow-mo, extra life)

ZONE_OFF = (235, 235, 235)    # target box, paddle not in it yet (neutral)
ZONE_ON = GOOD                # target box, paddle center inside
TARGET_LOCK_S = 0.25          # "lock-on" animation when the target locks (ball bounced)
BANNER_COLORS = {"good": GOOD, "bad": BAD, "power": POWER, "neutral": TEXT}

# Debug-overlay colors (only there)
WARN = (255, 210, 90)
STATE_COLORS = {gs.WAITING: (120, 180, 255), gs.READY: WARN, gs.PLAYING: GOOD,
                gs.EXTRA_LIFE: POWER, gs.GAME_OVER: BAD}


def P(x, y, z):
    """World (m) -> screen point (ints) for pygame."""
    sx, sy = view3d.project(x, y, z)
    return round(sx), round(sy)


def short_miss(miss_text) -> str:
    """The miss in one or two words, from GameState.miss_text."""
    text = (miss_text or "").upper()
    if text.startswith("SWING TOO LATE"):
        return "TOO LATE"
    if text.startswith("SWING TOO EARLY"):
        return "TOO EARLY"
    if text.startswith("SWING REJECTED"):
        return "SWING HARDER"
    if text.startswith("NO SWING"):
        return "SWING!"
    return "MISSED"           # out of zone, no contact


def banner_length(kind: str, text: str) -> float:
    """How long a banner stays: GO! and the miss word are quicker."""
    if text == "GO!":
        return config.SERVE_GO_SHOW_S
    if kind == "bad":
        return config.MISS_FLASH_S
    return config.BANNER_S


class Renderer:
    def __init__(self, screen: pygame.Surface, debug: bool = False):
        self.screen = screen
        self.debug = debug                   # D toggles (game.py)
        self.font = pygame.font.SysFont(None, 36)
        self.mid_font = pygame.font.SysFont(None, 52)
        self.big_font = pygame.font.SysFont(None, 96)
        self.huge_font = pygame.font.SysFont(None, 150)
        self.small_font = pygame.font.SysFont(None, 26)
        self.tiny_font = pygame.font.SysFont(None, 20)
        self._score_shown = None     # volley count last drawn, to start the pop
        self._score_pop_at = -1e9    # pygame ticks (ms) of the last increase
        self._shown_target = None    # target as drawn: glides toward the real one
        self._lock_time = None       # game_time the current target locked

    def draw(self, game: gs.GameState, paddle_src=None, camera_view=None, camera_error=None,
             mqtt_status=None, swing_info=None, sound_notes=None, banner=None, now=None,
             play_again=(False, False), voice=None):
        """Draw one frame.

        paddle_src: the paddle source (camera calibration, tracking).
        camera_view: Surface with the camera picture and overlays, or None.
        camera_error: text if the camera failed (always shown).
        banner: (text, kind, age s) of the one current message, or None;
                kind = "good" / "bad" / "power" / "neutral".
        play_again: (a real swing can play again, tags are on).
        voice: (listener status, (note, "ok"/"info"/"no", time) or None), or None.
        mqtt_status, swing_info, sound_notes: debug overlay only.
        """
        calib = getattr(paddle_src, "calib_info", None)
        tags_on = camera_view is not None or camera_error is not None
        self._draw_scene(game)
        if game.state != gs.WAITING:
            self._draw_hud(game)
            if not self.debug and swing_info is not None:
                self._draw_swing_force(swing_info)
        # The target box and my paddle go ON TOP of the volley count: both are
        # see-through, so the count stays readable and the paddle stays visible
        # when the ball crosses high (behind the count).
        self._draw_target(game)
        self._draw_my_paddle(game)
        size = ("full" if calib is not None or self.debug
                else "medium" if game.state == gs.WAITING else "small")
        self._draw_camera(camera_view, size)

        # A fresh miss word shows on its own before the EXTRA LIFE / GAME OVER screen
        miss_flash = banner is not None and banner[1] == "bad" and banner[2] < config.MISS_FLASH_S
        if calib is not None:
            self._draw_calibration(*calib)
        elif game.state == gs.WAITING:
            self._draw_waiting(game, tags_on)
        elif game.state == gs.READY:
            self._draw_serve_count(game)
        elif game.state == gs.EXTRA_LIFE and not miss_flash:
            self._draw_extra_life(game)
        elif game.state == gs.GAME_OVER and not miss_flash:
            self._draw_game_over(game, play_again[0])
        if game.paused:
            self._draw_paused()
        if banner is not None and banner[2] < banner_length(banner[1], banner[0]):
            self._draw_banner(*banner)
        if voice is not None:
            self._draw_voice(*voice, now)
        if camera_error:
            self._center_text(camera_error, self.font, BAD, self.screen.get_height() * 0.9)
        if self.debug:
            self._draw_debug(game, paddle_src, mqtt_status, swing_info, sound_notes, now)

    # ------------------------------------------------------------ playing HUD
    def _draw_hud(self, game: gs.GameState):
        """The volley count (top center), BEST (top right), card icons."""
        w = self.screen.get_width()
        if game.state != gs.GAME_OVER:      # (GAME OVER shows the final score big)
            self._draw_volleys(game)
        best = self.font.render(f"BEST {game.best_streak}", True, DIM_TEXT)
        self.screen.blit(best, (w - best.get_width() - 22, 18))
        self._draw_card_icons(game, w - best.get_width() - 60, 30)

    def _draw_volleys(self, game: gs.GameState):
        """The volley count, big at the top center: pops on every hit, green
        while it's at or above the best."""
        w = self.screen.get_width()
        count = game.streak
        now = pygame.time.get_ticks()
        if self._score_shown is not None and count > self._score_shown:
            self._score_pop_at = now
        self._score_shown = count
        age = (now - self._score_pop_at) / 1000
        pop = max(0.0, 1 - age / config.SCORE_POP_S) if config.SCORE_POP_S > 0 else 0
        color = GOOD if game.streak > 0 and game.streak >= game.best_streak else TEXT
        num = self.huge_font.render(str(count), True, color)
        if pop > 0:
            num = pygame.transform.smoothscale_by(num, 1 + 0.3 * pop)
        self.screen.blit(num, num.get_rect(center=(w / 2, 72)))

    def _draw_card_icons(self, game: gs.GameState, right_x: float, y: float):
        """The power-ups still available this rally, right to left, no labels:
        a heart (extra life), a clock (slow-mo; while it runs, dots for the
        balls left) and a bolt (voice SMASH; glowing while armed)."""
        x = right_x
        if game.smash_ready or not game.smash_used:
            if game.smash_ready:   # armed: a pulsing ring
                pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() / 90)
                pygame.draw.circle(self.screen, POWER, (x, y + 2), int(17 + 4 * pulse), 2)
            self._bolt(x, y + 2, POWER)
            x -= 42
        if not game.extra_life_used:
            self._heart(x, y, 9, POWER)
            x -= 42
        if game.slowmo_left > 0:
            self._clock(x, y, 13, POWER)
            for i in range(game.slowmo_left):
                pygame.draw.circle(self.screen, POWER, (x - 8 + i * 8, y + 22), 3)
        elif not game.slowmo_used:
            self._clock(x, y, 13, POWER)

    def _bolt(self, x, y, color):
        pts = [(x + 3, y - 14), (x - 7, y + 2), (x - 1, y + 2), (x - 4, y + 14), (x + 7, y - 3), (x + 1, y - 3)]
        pygame.draw.polygon(self.screen, color, pts)

    def _draw_swing_force(self, info: dict):
        """Bottom right: the last swing's force ("4.2 g") and a small bar, bright
        for a moment after each swing. (Everything else about swings: debug.)"""
        if info.get("peak") is None:
            return
        w, h = self.screen.get_size()
        age = info.get("age_s")
        fresh = age is not None and age < config.SWING_METER_FLASH_S
        color = TEXT if fresh else FAINT
        bar = pygame.Rect(w - 30, h - 96, 10, 80)
        pygame.draw.rect(self.screen, (40, 46, 62), bar, border_radius=4)
        frac = min(1.0, max(0.0, info["peak"] / config.SWING_METER_MAX_G))
        fill = pygame.Rect(bar.x, bar.bottom - int(bar.height * frac), bar.width, int(bar.height * frac))
        pygame.draw.rect(self.screen, GOOD if fresh else FAINT, fill, border_radius=4)
        label = self.font.render(f"{info['peak']:.1f} g", True, color)
        self.screen.blit(label, label.get_rect(bottomright=(bar.x - 10, bar.bottom)))

    def _draw_voice(self, status: str, note, now):
        """Bottom center: a small mic (green = listening, grey = ignoring the
        game's own sounds, red = no mic / no model) and what became of the last
        command: "smash: armed" (white), "pause: after this ball" (grey),
        "smash: used" / "pause: not sure" (red)."""
        if status == "off":
            return
        w, h = self.screen.get_size()
        x, y = w / 2, h - 30
        color = {"listening": GOOD, "muted": FAINT, "loading": FAINT}.get(status, BAD)
        pygame.draw.rect(self.screen, color, (x - 5, y - 13, 10, 17), border_radius=5)
        pygame.draw.arc(self.screen, color, (x - 9, y - 9, 18, 16), 3.3, 6.1, 2)
        pygame.draw.line(self.screen, color, (x, y + 7), (x, y + 11), 2)
        if status in ("no mic", "no model"):
            pygame.draw.line(self.screen, BAD, (x - 10, y - 14), (x + 10, y + 10), 2)
        if note is not None and now is not None and now - note[2] < config.VOICE_SHOW_S:
            color = {"ok": TEXT, "info": DIM_TEXT}.get(note[1], BAD)
            text = self.font.render(note[0], True, color)
            self.screen.blit(text, text.get_rect(midleft=(x + 18, y - 2)))

    def _draw_paused(self):
        w, h = self.screen.get_size()
        self._shade(170)
        self._center_text("PAUSED", self.big_font, TEXT, h * 0.40)
        self._center_text('Say "resume" or press P', self.font, DIM_TEXT, h * 0.52)

    def _heart(self, x, y, r, color, width=0):
        pygame.draw.circle(self.screen, color, (x - r + 1, y - 3), r, width)
        pygame.draw.circle(self.screen, color, (x + r - 1, y - 3), r, width)
        pygame.draw.polygon(self.screen, color, [(x - 2 * r + 1, y), (x + 2 * r - 1, y), (x, y + 2 * r)], width)

    def _clock(self, x, y, r, color):
        pygame.draw.circle(self.screen, color, (x, y), r, 3)
        pygame.draw.line(self.screen, color, (x, y), (x, y - r + 4), 3)
        pygame.draw.line(self.screen, color, (x, y), (x + r - 5, y + 2), 3)

    # ------------------------------------------------------------ one message at a time
    def _draw_banner(self, text: str, kind: str, age: float):
        """Big, short, quick: pops in over 0.1 s, fades over the last 40%."""
        w, h = self.screen.get_size()
        length = banner_length(kind, text)
        grow = min(1.0, age / 0.1)
        fade = min(1.0, (length - age) / (0.4 * length))
        color = BANNER_COLORS.get(kind, TEXT)
        surf = self.huge_font.render(text, True, color)
        surf = pygame.transform.smoothscale_by(surf, 0.7 + 0.3 * grow)
        surf.set_alpha(int(255 * max(0.0, fade)))
        shadow = self.huge_font.render(text, True, (0, 0, 0))
        shadow = pygame.transform.smoothscale_by(shadow, 0.7 + 0.3 * grow)
        shadow.set_alpha(int(140 * max(0.0, fade)))
        y = h * 0.42
        self.screen.blit(shadow, shadow.get_rect(center=(w / 2 + 4, y + 4)))
        self.screen.blit(surf, surf.get_rect(center=(w / 2, y)))

    # ------------------------------------------------------------ screens
    def _shade(self, alpha: int):
        shade = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        shade.fill((8, 10, 20, alpha))
        self.screen.blit(shade, (0, 0))

    def _draw_waiting(self, game: gs.GameState, tags_on: bool):
        """Start screen: the title, how to play in 3 steps with icons, and how to start."""
        w, h = self.screen.get_size()
        self._shade(150)
        self._center_text("PING PONG", self.big_font, TEXT, h * 0.17)
        steps = [(self._icon_target, "Move your hand", "to the target"),
                 (self._icon_swing, "Swing!", ""),
                 (self._icon_rally, "Keep the rally", "going")]
        for i, (icon, line1, line2) in enumerate(steps):
            cx = w / 2 + (i - 1) * 300
            pygame.draw.circle(self.screen, (32, 40, 60), (cx, h * 0.42), 70)
            icon(cx, h * 0.42)
            num = self.font.render(str(i + 1), True, DIM_TEXT)
            self.screen.blit(num, num.get_rect(center=(cx - 64, h * 0.42 - 58)))
            self._text_at(line1, self.font, TEXT, cx, h * 0.58)
            if line2:
                self._text_at(line2, self.font, TEXT, cx, h * 0.63)
        start = "Show a level card or press 1/2/3 to start" if tags_on else "Press 1/2/3 to start"
        self._center_text(start, self.mid_font, GOOD, h * 0.77)
        self._draw_level_chips(game.level, h * 0.86)

    def _draw_level_chips(self, current: int, y: float):
        """1 Beginner   2 Club   3 Pro, the current one highlighted."""
        keys = {lvl: key for key, lvl in config.KEY_LEVELS.items()}
        parts = [self.font.render(f"{keys.get(lvl, '')}  {v['name']}", True, TEXT if lvl == current else FAINT)
                 for lvl, v in sorted(config.LEVELS.items())]
        gap = 50
        x = (self.screen.get_width() - sum(p.get_width() for p in parts) - gap * (len(parts) - 1)) / 2
        for lvl, surf in zip(sorted(config.LEVELS), parts):
            rect = surf.get_rect(midleft=(x, y))
            if lvl == current:
                pygame.draw.rect(self.screen, (40, 50, 72), rect.inflate(22, 12), border_radius=10)
            self.screen.blit(surf, rect)
            x += surf.get_width() + gap

    # Icons for the how-to-play steps (drawn, so no font glyphs are needed)
    def _icon_target(self, cx, cy):
        box = pygame.Rect(0, 0, 70, 70)
        box.center = (cx + 8, cy)
        pygame.draw.rect(self.screen, GOOD, box, 3, border_radius=10)
        pygame.draw.circle(self.screen, GOOD, box.center, 6)
        # a hand (paddle dot) moving in from the left
        pygame.draw.circle(self.screen, PADDLE, (cx - 42, cy + 14), 11)
        pygame.draw.line(self.screen, TEXT, (cx - 28, cy + 8), (cx - 6, cy + 1), 3)
        pygame.draw.polygon(self.screen, TEXT, [(cx - 2, cy), (cx - 12, cy - 6), (cx - 10, cy + 6)])

    def _icon_swing(self, cx, cy):
        pygame.draw.circle(self.screen, PADDLE, (cx + 6, cy - 10), 24)
        pygame.draw.line(self.screen, HANDLE, (cx - 8, cy + 8), (cx - 26, cy + 30), 9)
        for r in (40, 52):   # motion arcs
            pygame.draw.arc(self.screen, TEXT, (cx - r + 6, cy - r - 10, 2 * r, 2 * r), 2.4, 3.5, 3)

    def _icon_rally(self, cx, cy):
        pygame.draw.line(self.screen, LINE, (cx - 46, cy + 26), (cx + 46, cy + 26), 3)   # table
        pygame.draw.line(self.screen, LINE, (cx, cy + 26), (cx, cy + 10), 3)             # net
        for i in range(7):   # the ball's arc over the net
            t = i / 6
            x = cx - 40 + 80 * t
            y = cy + 20 - 46 * 4 * t * (1 - t)
            pygame.draw.circle(self.screen, BALL, (x, y), 3 if i < 6 else 6)
        pygame.draw.polygon(self.screen, GOOD, [(cx + 30, cy - 30), (cx + 22, cy - 18), (cx + 38, cy - 18)])

    def _draw_calibration(self, step: str, number: int, seconds_left: float, note: str):
        """Calibration: one big prompt with an arrow, a countdown, 4 dots."""
        w, h = self.screen.get_size()
        self._shade(120)
        words = {"left": "Reach LEFT", "right": "Reach RIGHT", "high": "Reach UP", "low": "Reach DOWN"}
        text = self.big_font.render(words[step], True, TEXT)
        arrow_w = 90
        x = w / 2 - (text.get_width() + 30 + arrow_w) / 2
        y = h * 0.45
        self.screen.blit(text, text.get_rect(midleft=(x, y)))
        self._arrow(x + text.get_width() + 30 + arrow_w / 2, y, step, GOOD)
        self._center_text(str(max(1, math.ceil(seconds_left))), self.huge_font, GOOD, h * 0.66)
        if note:
            self._center_text(note, self.mid_font, BAD, h * 0.80)
        for i in range(4):   # which of the 4 steps
            pygame.draw.circle(self.screen, TEXT if i < number else FAINT,
                               (w / 2 + (i - 1.5) * 26, h * 0.30), 7 if i == number - 1 else 5)

    def _arrow(self, cx, cy, step, color):
        """A thick arrow pointing left / right / up / down."""
        d = {"left": (-1, 0), "right": (1, 0), "high": (0, -1), "low": (0, 1)}[step]
        dx, dy = d
        L, head = 44, 26
        tail = (cx - dx * L, cy - dy * L)
        tip = (cx + dx * L, cy + dy * L)
        pygame.draw.line(self.screen, color, tail, (cx + dx * (L - head), cy + dy * (L - head)), 14)
        px, py = -dy, dx   # perpendicular
        base = (cx + dx * (L - head), cy + dy * (L - head))
        pygame.draw.polygon(self.screen, color, [tip, (base[0] + px * head, base[1] + py * head),
                                                 (base[0] - px * head, base[1] - py * head)])

    def _draw_serve_count(self, game: gs.GameState):
        """"3, 2, 1" before each serve: one big number, shrinking through its second."""
        h = self.screen.get_height()
        step = config.SERVE_COUNT_STEP_S
        frac = (game.ready_left % step) / step if step > 0 else 0     # 1 -> 0 through each number
        num = self.huge_font.render(str(game.ready_number), True, TEXT)
        num = pygame.transform.smoothscale_by(num, 1.0 + 0.6 * frac)
        self.screen.blit(num, num.get_rect(center=(self.screen.get_width() / 2, h * 0.42)))

    def _draw_extra_life(self, game: gs.GameState):
        """Paused after a miss: EXTRA LIFE? Show your card + the countdown."""
        w, h = self.screen.get_size()
        self._shade(170)
        title = self.big_font.render("EXTRA LIFE?", True, POWER)
        rect = title.get_rect(center=(w / 2 + 34, h * 0.28))
        self.screen.blit(title, rect)
        self._heart(rect.left - 46, rect.centery - 6, 16, POWER)
        self._center_text("Show your card", self.mid_font, TEXT, h * 0.38)
        cx, cy = w / 2, h * 0.62
        frac = game.countdown_left - int(game.countdown_left)   # a ring emptying each second
        pygame.draw.circle(self.screen, (40, 48, 70), (cx, cy), 95, 10)
        if frac > 0:
            pygame.draw.arc(self.screen, POWER, (cx - 95, cy - 95, 190, 190),
                            1.5708, 1.5708 + 6.2832 * frac, 10)
        num = self.huge_font.render(str(game.countdown_number), True, TEXT)
        self.screen.blit(num, num.get_rect(center=(cx, cy)))

    def _draw_game_over(self, game: gs.GameState, swing_ok: bool):
        """GAME OVER: the score, the best (or NEW HIGH SCORE!), and how to play
        again (greyed for the first GAME_OVER_SWING_IGNORE_S)."""
        w, h = self.screen.get_size()
        self._shade(205)
        self._center_text("GAME OVER", self.big_font, BAD, h * 0.16)
        num = self.huge_font.render(str(game.last_rally), True, GOOD if game.new_high_score else TEXT)
        self.screen.blit(num, num.get_rect(center=(w / 2, h * 0.38)))
        if game.new_high_score:
            self._center_text("NEW HIGH SCORE!", self.mid_font, GOOD, h * 0.53)
        else:
            self._center_text(f"BEST {game.best_streak}", self.mid_font, DIM_TEXT, h * 0.53)
        again = "Swing to play again" if swing_ok else "Press SPACE to play again"
        self._center_text(again, self.mid_font, TEXT if game.can_play_again else FAINT, h * 0.72)

    # ------------------------------------------------------------ camera view
    def _draw_camera(self, view, size: str):
        """Camera view: "full" top right (calibration, debug), "medium" bottom
        right (start screen, under the how-to steps), "small" tucked into the
        bottom-left corner during play."""
        if view is None:
            return
        w, h = self.screen.get_size()
        if size == "small":
            view = pygame.transform.smoothscale_by(view, config.CAMERA_PREVIEW_SMALL_WIDTH / view.get_width())
            x, y = 16, h - view.get_height() - 16
        elif size == "medium":
            view = pygame.transform.smoothscale_by(view, 0.75)
            x, y = w - view.get_width() - 16, h - view.get_height() - 16
        else:
            x, y = w - view.get_width() - 20, 92
        self.screen.blit(view, (x, y))
        pygame.draw.rect(self.screen, WALL, (x - 1, y - 1, view.get_width() + 2, view.get_height() + 2), 1)

    # ------------------------------------------------------------ debug overlay (D)
    def _draw_debug(self, game, paddle_src, mqtt_status, swing_info, sound_notes, now):
        """Everything for tuning and diagnosis, on top of the normal screen."""
        h = self.screen.get_height()
        lines = [(game.state, STATE_COLORS.get(game.state, TEXT))]
        level = config.LEVELS[game.level]
        checks = "left/right only" if game.hit_tolerance[1] is None else "left/right + height"
        lines.append((f"{level['name']} ({checks})  speed x{game.speed_factor:.2f} "
                      f"({game.ball_speed:.2f} m/s, pace {game.time_scale:.2f})", DIM_TEXT))
        if paddle_src is not None and getattr(paddle_src, "kind", "") == "camera":
            lines.append((f"tracking: {paddle_src.tracking}", TEXT))
        if paddle_src is not None and paddle_src.message:
            lines += [(line, WARN) for line in paddle_src.message.split("\n")]
        if game.miss_text:
            lines.append(("last miss: " + "  ".join(game.miss_text.split("\n")), WARN))
        if now is not None and game.card_note and now - game.card_note[1] < config.CARD_NOTE_S:
            lines.append((game.card_note[0], WARN))
        lines += [(f"sound {s}", DIM_TEXT) for s in (sound_notes or [])]
        panel = pygame.Surface((660, 12 + 22 * len(lines)), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 150))
        self.screen.blit(panel, (12, 110))
        for i, (text, color) in enumerate(lines):
            self.screen.blit(self.small_font.render(text, True, color), (20, 116 + 22 * i))
        self._draw_mqtt(mqtt_status)
        if swing_info is not None:
            self._draw_swing_meter(game, swing_info)
        tag = self.tiny_font.render("DEBUG (D)", True, WARN)
        self.screen.blit(tag, (self.screen.get_width() / 2 - tag.get_width() / 2, h - 18))

    def _draw_swing_meter(self, game: gs.GameState, info: dict):
        """Bottom right: last swing's peak (bar), flashing on each new swing, plus
        its delay and the last hit's timing."""
        w, h = self.screen.get_size()
        bar = pygame.Rect(w - 52, h - 210, 22, 170)
        pygame.draw.rect(self.screen, (40, 46, 62), bar, border_radius=4)
        age = info["age_s"]
        flash = age is not None and age < config.SWING_METER_FLASH_S
        if info["peak"] is not None:
            frac = min(1.0, max(0.0, info["peak"] / config.SWING_METER_MAX_G))
            fill = pygame.Rect(bar.x, bar.bottom - int(bar.height * frac), bar.width,
                               int(bar.height * frac))
            pygame.draw.rect(self.screen, PADDLE, fill, border_radius=4)
        elif flash:   # Space swing: no measured peak, flash the whole bar
            pygame.draw.rect(self.screen, PADDLE, bar, border_radius=4)
        if flash:
            glow = pygame.Surface(bar.inflate(12, 12).size, pygame.SRCALPHA)
            alpha = int(200 * (1 - age / config.SWING_METER_FLASH_S))
            pygame.draw.rect(glow, (255, 230, 120, alpha), glow.get_rect(), 4, border_radius=8)
            self.screen.blit(glow, bar.inflate(12, 12).topleft)
        # Minimum swing line (the IMU's accel check)
        y_min = bar.bottom - int(bar.height * config.RETURN_PEAK_LOW_G / config.SWING_METER_MAX_G)
        pygame.draw.line(self.screen, TEXT, (bar.x - 4, y_min), (bar.right + 4, y_min), 1)

        modes = {"imu": "IMU swings", "imu+keyboard": "IMU or Space = swing",
                 "always": "--no-imu: every ball swung"}
        lines = [(modes.get(info["mode"], info["mode"]), DIM_TEXT)]
        if info["mode"] != "always":
            before, after = game.swing_window
            kind = "fast path" if game.swing_window == config.SWING_WINDOW_FAST else "slow path: hivemq"
            lines.append((f"swing window -{before * 1000:.0f}/+{after * 1000:.0f} ms ({kind})", DIM_TEXT))
        if info["peak"] is not None:
            gyro = f"  {info['peak_gyro']:.0f} dps" if info["peak_gyro"] is not None else ""
            lines.append((f"swing #{info['count']}: {info['peak']:.2f} g{gyro}", WARN if flash else TEXT))
            if info["delay_ms"] is not None and -2000 < info["delay_ms"] < 10000:
                via = f" via {info['via']}" if info.get("via") else ""
                lines.append((f"delay {info['delay_ms']:.0f} ms (board -> laptop{via})", TEXT))
        elif age is not None:
            lines.append(("swing (Space)", WARN if flash else TEXT))
        elif info["mode"] != "always":
            lines.append(("no swing yet", DIM_TEXT))
        if game.last_swing_offset is not None:
            lines.append((f"last hit: swing {game.last_swing_offset * 1000:+.0f} ms vs contact, "
                          f"return x{game.last_return_factor:.2f}", DIM_TEXT))
        y = bar.bottom - len(lines) * 24 + 4
        for text, color in lines:
            surf = self.small_font.render(text, True, color)
            self.screen.blit(surf, (bar.x - 16 - surf.get_width(), y))
            y += 24

    def _draw_mqtt(self, status):
        """MQTT status and record, bottom left (debug only)."""
        if status is None:
            text, color = "MQTT: off", DIM_TEXT
        else:
            connected, detail = status
            if connected:
                text, color = f"MQTT hivemq: connected  |  {detail}", GOOD
            else:
                text, color = f"MQTT hivemq: disconnected (retrying)  |  {detail}", BAD
        surf = self.small_font.render(text, True, color)
        self.screen.blit(surf, (20, self.screen.get_height() - surf.get_height() - 30))

    # ------------------------------------------------------------ 3D scene
    def _draw_scene(self, game: gs.GameState):
        """First-person table view, drawn back to front (see view3d.py)."""
        s = self.screen
        w, h = s.get_size()
        s.fill(BACKGROUND)
        pygame.draw.rect(s, FLOOR, (0, config.VIEW_HORIZON_Y, w, h - config.VIEW_HORIZON_Y))

        L, W = config.TABLE_LENGTH, config.TABLE_WIDTH
        self._draw_opponent(game.opponent)
        # Table top, its front face, and the white lines
        top = [P(-W / 2, 0, 0), P(W / 2, 0, 0), P(W / 2, 0, L), P(-W / 2, 0, L)]
        front = [P(-W / 2, 0, 0), P(W / 2, 0, 0), P(W / 2, -0.04, 0), P(-W / 2, -0.04, 0)]
        pygame.draw.polygon(s, TABLE_EDGE, front)
        pygame.draw.polygon(s, TABLE, top)
        pygame.draw.lines(s, LINE, True, top, 3)
        pygame.draw.line(s, LINE, P(0, 0, 0), P(0, 0, L), 1)

        ball_visible = game.state != gs.WAITING
        if ball_visible:
            self._draw_bounce_mark(game)
            self._draw_shadow(game.ball)
        beyond_net = game.ball.z > L / 2
        if ball_visible and beyond_net:
            self._draw_ball(game)
        self._draw_net()
        if ball_visible and not beyond_net:
            self._draw_ball(game)
        # (the target box and my paddle are drawn by draw(), over the HUD)

    def _draw_net(self):
        L, W, H = config.TABLE_LENGTH, config.TABLE_WIDTH, config.NET_HEIGHT
        x0, x1 = -W / 2 - 0.08, W / 2 + 0.08
        quad = [P(x0, 0, L / 2), P(x1, 0, L / 2), P(x1, H, L / 2), P(x0, H, L / 2)]
        net = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        pygame.draw.polygon(net, NET, quad)
        self.screen.blit(net, (0, 0))
        pygame.draw.line(self.screen, LINE, P(x0, H, L / 2), P(x1, H, L / 2), 3)
        for x in (x0, x1):
            pygame.draw.line(self.screen, POST, P(x, 0, L / 2), P(x, H, L / 2), 3)

    def _draw_shadow(self, b: gs.Ball):
        """Dark ellipse on the table under the ball: smaller and fainter the higher it is."""
        if not gs.on_table(b.x, b.z) or b.y < 0:
            return
        sx, sy = P(b.x, 0, b.z)
        r = view3d.size(config.BALL_RADIUS_M, b.z) * max(0.4, 1 - b.y)
        alpha = int(120 * max(0.25, 1 - b.y * 1.5))
        shadow = pygame.Surface((int(r * 2.4) + 2, int(r * 1.0) + 2), pygame.SRCALPHA)
        pygame.draw.ellipse(shadow, (0, 0, 0, alpha), shadow.get_rect())
        self.screen.blit(shadow, shadow.get_rect(center=(sx, sy)))

    def _draw_ball(self, game: gs.GameState):
        b = game.ball
        sx, sy = P(b.x, b.y, b.z)
        r = max(2, view3d.size(config.BALL_RADIUS_M, b.z))
        # Slow-mo: the ball takes the power-up color while it lasts
        pygame.draw.circle(self.screen, POWER if game.slowmo_left > 0 else BALL, (sx, sy), r)
        pygame.draw.circle(self.screen, BALL_EDGE, (sx, sy), r, 1)
        if game.smash_armed:   # voice SMASH armed for this ball: a bright ring
            pygame.draw.circle(self.screen, POWER, (sx, sy), r + 5, 3)
        if game.holding:
            # Waiting for a late swing: a ring closes in on the ball as the
            # timing window runs out.
            ring = r + (1 - game.hold_progress) * r * 2.5
            pygame.draw.circle(self.screen, TEXT, (sx, sy), ring, 2)

    def _draw_bounce_mark(self, game: gs.GameState):
        """A short-lived ring where the ball last bounced."""
        if game.last_bounce is None:
            return
        x, z, t = game.last_bounce
        age = game.game_time - t
        if age > 0.35:
            return
        r = view3d.size(0.03 + age * 0.15, z)
        sx, sy = P(x, 0, z)
        ring = pygame.Surface((int(r * 2) + 4, int(r) + 4), pygame.SRCALPHA)
        pygame.draw.ellipse(ring, (255, 255, 255, int(160 * (1 - age / 0.35))), ring.get_rect(), 2)
        self.screen.blit(ring, ring.get_rect(center=(sx, sy)))

    def _draw_paddle_shape(self, x, y, z, color, alpha, radius_m=config.PADDLE_RADIUS_M):
        """A round paddle with a handle, as a see-through disk."""
        sx, sy = P(x, y, z)
        r = view3d.size(radius_m, z)
        layer = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        hx, hy = P(x, y - radius_m * 1.9, z)
        pygame.draw.line(layer, (*HANDLE, alpha), (sx, sy + r * 0.8), (hx, hy), max(3, int(r * 0.22)))
        pygame.draw.circle(layer, (*color, alpha), (sx, sy), r)
        pygame.draw.circle(layer, (*color, 255), (sx, sy), r, 2)
        self.screen.blit(layer, (0, 0))

    def _draw_opponent(self, opp: gs.Opponent):
        self._draw_paddle_shape(opp.x, config.OPP_HIT_HEIGHT, config.OPP_HIT_Z, OPPONENT, 220)

    def _draw_my_paddle(self, game: gs.GameState):
        p = game.paddle
        z = config.MY_HIT_Z
        self._draw_paddle_shape(p.x, p.y, z, PADDLE, 50)   # very see-through: never hide the ball
        # The paddle's center: the point that must be inside the target box
        cx, cy = P(p.x, p.y, z)
        pygame.draw.circle(self.screen, HIT_RING, (cx, cy), 5)
        pygame.draw.line(self.screen, HIT_RING, (cx - 12, cy), (cx + 12, cy), 1)
        pygame.draw.line(self.screen, HIT_RING, (cx, cy - 12), (cx, cy + 12), 1)

    def _draw_target(self, game: gs.GameState):
        """Where the ball will cross my hitting plane, with the level's hit zone
        around it: get the paddle's center inside the box. Faint while only
        predicted, solid with a lock-on ring once the ball bounced on my side;
        green (GOOD) when the paddle is in position. On levels that ignore
        height the box is a full-height band."""
        if game.target is None or game.state != gs.PLAYING:
            self._shown_target, self._lock_time = None, None
            return
        tx, ty = game.target
        if self._shown_target is None:
            self._shown_target = (tx, ty)
        else:   # glide to a corrected prediction instead of jumping
            sx0, sy0 = self._shown_target
            self._shown_target = (sx0 + 0.35 * (tx - sx0), sy0 + 0.35 * (ty - sy0))
        tx, ty = self._shown_target
        if game.target_locked and self._lock_time is None:
            self._lock_time = game.game_time
        locked = self._lock_time is not None

        z = config.MY_HIT_Z
        rx, ry = game.zone
        top = ty + ry if ry is not None else config.PADDLE_Y_MAX_M + config.PADDLE_RADIUS_M
        bottom = ty - ry if ry is not None else config.PADDLE_Y_MIN_M - config.PADDLE_RADIUS_M
        x0, y0 = P(tx - rx, top, z)
        x1, y1 = P(tx + rx, bottom, z)
        box = pygame.Rect(x0, y0, x1 - x0, y1 - y0)
        on = gs.in_zone(tx, ty, game.paddle, game.zone)
        color = ZONE_ON if on else ZONE_OFF
        layer = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        pygame.draw.rect(layer, (*color, 55 if locked else 20), box, border_radius=8)
        pygame.draw.rect(layer, (*color, 255 if locked else 100), box, 3 if locked else 1, border_radius=8)
        # Crosshair on the exact crossing spot
        cx, cy = P(tx, ty, z)
        r = view3d.size(config.BALL_RADIUS_M, z)
        alpha = 255 if locked else 130
        pygame.draw.circle(layer, (*color, alpha), (cx, cy), r + 3, 2)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            pygame.draw.line(layer, (*color, alpha), (cx + dx * (r + 6), cy + dy * (r + 6)),
                             (cx + dx * (r + 18), cy + dy * (r + 18)), 2)
        if locked:   # lock-on: a ring closing in on the box for TARGET_LOCK_S
            age = game.game_time - self._lock_time
            if age < TARGET_LOCK_S:
                grow = int((1 - age / TARGET_LOCK_S) * 60)
                pygame.draw.rect(layer, (*color, 200), box.inflate(grow, grow), 2, border_radius=10)
        self.screen.blit(layer, (0, 0))

    # ------------------------------------------------------------ helpers
    def _center_text(self, text, font, color, y):
        self._text_at(text, font, color, self.screen.get_width() / 2, y)

    def _text_at(self, text, font, color, x, y):
        surf = font.render(text, True, color)
        self.screen.blit(surf, surf.get_rect(center=(x, y)))
