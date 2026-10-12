"""Drawing only: turns a GameState into pixels. No game rules in here.

The table scene is drawn in first person with view3d.project(); the HUD
(state, scores, camera view, MQTT, swing meter) is flat on top.
"""
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
TEXT = (230, 230, 230)
DIM_TEXT = (150, 160, 180)
CALIBRATE = (255, 210, 90)
ZONE_OFF = (255, 170, 60)     # target zone, paddle not in it yet
ZONE_ON = (90, 230, 120)      # target zone, paddle center inside: in position
TARGET_LOCK_S = 0.25          # "lock-on" animation when the target locks (ball bounced)
STATE_COLORS = {gs.WAITING: (120, 180, 255), gs.PLAYING: (120, 220, 140),
                gs.MISS: (255, 110, 110), gs.EXTRA_LIFE: (255, 120, 160)}
GOLD = (255, 205, 70)
SLOWMO_BLUE = (110, 170, 255)
HEART = (255, 90, 130)
BANNER_COLORS = {"record": GOLD, "slowmo": SLOWMO_BLUE, "life": HEART}


def P(x, y, z):
    """World (m) -> screen point (ints) for pygame."""
    sx, sy = view3d.project(x, y, z)
    return round(sx), round(sy)


class Renderer:
    def __init__(self, screen: pygame.Surface):
        self.screen = screen
        self.font = pygame.font.SysFont(None, 36)
        self.big_font = pygame.font.SysFont(None, 96)
        self.reason_font = pygame.font.SysFont(None, 64)
        self.small_font = pygame.font.SysFont(None, 26)
        self.tiny_font = pygame.font.SysFont(None, 20)
        self.score_font = pygame.font.SysFont(None, 130)
        self._score_shown = None     # volley count last drawn, to start the pop
        self._score_pop_at = -1e9    # pygame ticks (ms) of the last increase
        self._shown_target = None    # target as drawn: glides toward the real one
        self._lock_time = None       # game_time the current target locked

    def draw(self, game: gs.GameState, paddle_src=None, camera_view=None, camera_error=None,
             mqtt_status=None, swing_info=None, sound_notes=None, banner=None, now=None):
        """Draw one frame.

        paddle_src (optional) adds its messages ("no hand", calibration).
        camera_view: Surface with the camera picture and overlays, or None.
        camera_error: text if the camera failed, shown instead of the view.
        mqtt_status: (connected, detail text) for the status line, None = MQTT off.
        swing_info: dict for the swing meter (game.swing_display), or None.
        """
        tags_on = camera_view is not None or camera_error is not None
        self._draw_game(game, paddle_src, tags_on)
        self._draw_camera(camera_view)
        if paddle_src is not None:
            self._draw_paddle_messages(game, paddle_src, camera_error)
        self._draw_mqtt(mqtt_status)
        if swing_info is not None:
            self._draw_swing_meter(game, swing_info)
        if sound_notes:
            self._draw_sound_notes(sound_notes)
        if game.state == gs.EXTRA_LIFE:
            self._draw_countdown(game)
        if now is not None and game.card_note and now - game.card_note[1] < config.CARD_NOTE_S:
            self._center_text(game.card_note[0], self.font, CALIBRATE, self.screen.get_height() * 0.62)
        if banner is not None and banner[2] < config.HIGH_SCORE_BANNER_S:
            self._draw_banner(*banner)

    def _draw_sound_notes(self, notes):
        """Last few sounds and why they played (left, under the tracking badge)."""
        for i, text in enumerate(notes):
            surf = self.small_font.render(f"sound {text}", True, DIM_TEXT)
            self.screen.blit(surf, (20, 118 + 22 * i))

    def _draw_countdown(self, game: gs.GameState):
        """Extra-life offer: the game is paused, a big number counts down."""
        w, h = self.screen.get_size()
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill((8, 10, 20, 170))
        self.screen.blit(shade, (0, 0))
        self._center_text("EXTRA LIFE?  Show your card!", self.big_font, HEART, h * 0.30)
        cx, cy = w / 2, h * 0.58
        # A ring that empties over each second, around the number
        frac = game.countdown_left - int(game.countdown_left)
        pygame.draw.circle(self.screen, (60, 40, 55), (cx, cy), 95, 10)
        if frac > 0:
            pygame.draw.arc(self.screen, HEART, (cx - 95, cy - 95, 190, 190),
                            1.5708, 1.5708 + 6.2832 * frac, 10)
        num = self.score_font.render(str(game.countdown_number), True, (255, 255, 255))
        self.screen.blit(num, num.get_rect(center=(cx, cy)))
        self._center_text(f"volley count {game.streak} is waiting", self.small_font, DIM_TEXT, h * 0.76)

    def _draw_banner(self, text: str, kind: str, age: float):
        """Big banner ("HIGH SCORE!", "SLOW-MO!", "EXTRA LIFE!"), popping in, then fading out."""
        w = self.screen.get_width()
        grow = min(1.0, age / 0.15)                                  # pops in over 0.15 s
        fade = min(1.0, (config.HIGH_SCORE_BANNER_S - age) / 0.4)    # fades over the last 0.4 s
        color = BANNER_COLORS.get(kind, GOLD)
        text = self.big_font.render(text, True, color)
        scale = 0.6 + 0.4 * grow
        text = pygame.transform.smoothscale_by(text, scale)
        text.set_alpha(int(255 * fade))
        panel = pygame.Surface((w, text.get_height() + 24), pygame.SRCALPHA)
        panel.fill((color[0] // 6, color[1] // 6, color[2] // 6, int(170 * fade)))   # dark tint of the text color
        y = 190
        self.screen.blit(panel, (0, y - panel.get_height() / 2))
        self.screen.blit(text, text.get_rect(center=(w / 2, y)))

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
            lines.append((f"swing #{info['count']}: {info['peak']:.2f} g{gyro}",
                          CALIBRATE if flash else TEXT))
            if info["delay_ms"] is not None and -2000 < info["delay_ms"] < 10000:
                via = f" via {info['via']}" if info.get("via") else ""
                lines.append((f"delay {info['delay_ms']:.0f} ms (board -> laptop{via})", TEXT))
        elif age is not None:
            lines.append(("swing (Space)", CALIBRATE if flash else TEXT))
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
        """MQTT status and record, bottom left."""
        if status is None:
            text, color = "MQTT: off", DIM_TEXT
        else:
            connected, detail = status
            if connected:
                text, color = f"MQTT hivemq: connected  |  {detail}", STATE_COLORS[gs.PLAYING]
            else:
                text, color = f"MQTT hivemq: disconnected (retrying)  |  {detail}", STATE_COLORS[gs.MISS]
        surf = self.small_font.render(text, True, color)
        self.screen.blit(surf, (20, self.screen.get_height() - surf.get_height() - 12))

    def _draw_game(self, game: gs.GameState, paddle_src, tags_on: bool):
        h = self.screen.get_height()
        self._draw_scene(game)
        self._draw_hud(game)
        if game.state == gs.WAITING:
            self._draw_waiting(game, paddle_src, tags_on)
        elif game.state == gs.MISS:
            self._center_text("MISS", self.big_font, STATE_COLORS[gs.MISS], h * 0.265)
            if game.miss_text:   # why: big enough to read mid-rally
                lines = game.miss_text.split("\n")   # 2nd line: details (e.g. which way)
                panel = pygame.Surface((self.screen.get_width(), 70 + 40 * (len(lines) - 1)),
                                       pygame.SRCALPHA)
                panel.fill((10, 14, 24, 190))
                self.screen.blit(panel, (0, h * 0.36 - 35))
                for i, line in enumerate(lines):
                    self._center_text(line, self.reason_font if i == 0 else self.font, CALIBRATE,
                                      h * 0.36 + (0 if i == 0 else 22 + 38 * i))

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
        self._draw_target(game)
        self._draw_my_paddle(game)

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
        # Slow-mo: the ball is tinted blue while it lasts
        pygame.draw.circle(self.screen, (150, 200, 255) if game.slowmo_left > 0 else BALL, (sx, sy), r)
        pygame.draw.circle(self.screen, BALL_EDGE, (sx, sy), r, 1)
        if game.holding:
            # Waiting for a late swing: a ring closes in on the ball as the
            # timing window runs out.
            ring = r + (1 - game.hold_progress) * r * 2.5
            pygame.draw.circle(self.screen, CALIBRATE, (sx, sy), ring, 2)

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
        green when the paddle is in position. On levels that ignore height the
        box is a full-height band."""
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
        pygame.draw.rect(layer, (*color, 55 if locked else 22), box, border_radius=8)
        pygame.draw.rect(layer, (*color, 255 if locked else 110), box, 3 if locked else 1, border_radius=8)
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

    def _draw_hud(self, game: gs.GameState):
        state = self.font.render(game.state, True, STATE_COLORS[game.state])
        self.screen.blit(state, (20, 16))
        level = config.LEVELS[game.level]
        checks = "left/right only" if game.hit_tolerance[1] is None else "left/right + height"
        lvl = self.small_font.render(f"Level {game.level}: {level['name']} ({checks})", True, DIM_TEXT)
        self.screen.blit(lvl, (20, 52))

        w = self.screen.get_width()
        self._draw_volleys(game)
        self._draw_card_icons(game)
        # Best (smaller, top right) and the rally's speed: "Speed x1.40" + bar
        best = self.font.render(f"Best: {game.best_streak}", True, TEXT)
        self.screen.blit(best, (w - best.get_width() - 20, 14))
        factor, top = game.speed_factor, game.max_speed_factor
        label = self.small_font.render(f"Speed x{factor:.2f}", True, TEXT if factor > 1 else DIM_TEXT)
        self.screen.blit(label, (w - label.get_width() - 20, 46))
        bar = pygame.Rect(w - 20 - 150, 70, 150, 8)
        pygame.draw.rect(self.screen, (40, 46, 62), bar, border_radius=4)
        frac = 0 if top <= 1 else (factor - 1) / (top - 1)
        if frac > 0:
            color = GOLD if frac >= 1 else (255, int(200 - 120 * frac), 80)   # warmer as it speeds up
            pygame.draw.rect(self.screen, color, (bar.x, bar.y, max(8, int(bar.width * frac)), bar.height),
                             border_radius=4)

    def _draw_card_icons(self, game: gs.GameState):
        """Top right, left of Best: both power-up cards, always shown. Bright =
        available this rally, dim + "used" = spent (they come back when the
        rally ends), blue "x3" = slow-mo running with that many balls left."""
        y = 34
        dim = (90, 96, 112)
        xs, xh = self.screen.get_width() - 265, self.screen.get_width() - 205   # S icon, heart
        # SLOW-MO: a blue disc with an "S"
        active, used = game.slowmo_left > 0, game.slowmo_used
        color = SLOWMO_BLUE if (active or not used) else dim
        pygame.draw.circle(self.screen, color, (xs, y + 2), 15, 0 if not used or active else 3)
        letter = self.small_font.render("S", True, (10, 20, 40) if not used or active else dim)
        self.screen.blit(letter, letter.get_rect(center=(xs, y + 3)))
        caption = f"x{game.slowmo_left}" if active else "used" if used else "slow-mo"
        self._icon_caption(caption, SLOWMO_BLUE if active else dim if used else DIM_TEXT, xs, y + 30)
        # EXTRA LIFE: a heart (two circles + a triangle), outline when used
        r = 8
        heart = HEART if not game.extra_life_used else dim
        width = 0 if not game.extra_life_used else 2
        pygame.draw.circle(self.screen, heart, (xh - r + 1, y - 3), r, width)
        pygame.draw.circle(self.screen, heart, (xh + r - 1, y - 3), r, width)
        pygame.draw.polygon(self.screen, heart, [(xh - 2 * r + 1, y), (xh + 2 * r - 1, y), (xh, y + 2 * r)], width)
        self._icon_caption("used" if game.extra_life_used else "life", dim if game.extra_life_used else DIM_TEXT,
                           xh, y + 30)

    def _icon_caption(self, text, color, x, y):
        surf = self.tiny_font.render(text, True, color)
        self.screen.blit(surf, surf.get_rect(center=(x, y)))

    def _draw_volleys(self, game: gs.GameState):
        """The volley count, big at the top center: pops on every hit. During
        MISS it shows the rally that just ended (dimmed)."""
        w = self.screen.get_width()
        count = game.last_rally if game.state == gs.MISS else game.streak
        now = pygame.time.get_ticks()
        if self._score_shown is not None and count > self._score_shown:
            self._score_pop_at = now
        self._score_shown = count
        age = (now - self._score_pop_at) / 1000
        pop = max(0.0, 1 - age / config.SCORE_POP_S) if config.SCORE_POP_S > 0 else 0
        color = (STATE_COLORS[gs.MISS] if game.state == gs.MISS
                 else GOLD if game.streak > 0 and game.streak >= game.best_streak else TEXT)
        num = self.score_font.render(str(count), True, color)
        if pop > 0:
            num = pygame.transform.smoothscale_by(num, 1 + 0.3 * pop)
        self.screen.blit(num, num.get_rect(center=(w / 2, 70)))
        label = self.small_font.render("VOLLEYS", True, DIM_TEXT)
        self.screen.blit(label, label.get_rect(center=(w / 2, 128)))

    def _draw_waiting(self, game: gs.GameState, paddle_src, tags_on: bool):
        h = self.screen.get_height()
        ready = paddle_src is None or paddle_src.ready
        camera = paddle_src is not None and paddle_src.kind == "camera"
        panel = pygame.Surface((760, 250), pygame.SRCALPHA)
        panel.fill((10, 14, 24, 200))
        self.screen.blit(panel, panel.get_rect(center=(self.screen.get_width() / 2, h * 0.45)))
        self._center_text("PING PONG", self.big_font, TEXT, h * 0.32)
        if ready:
            start = ("Hold up a level tag (0-2) for 1 s, or press SPACE" if tags_on
                     else "Press SPACE to start")
            self._center_text(start, self.font, STATE_COLORS[gs.WAITING], h * 0.46)
        levels = "   ".join(f"[{n}] {v['name']}" for n, v in sorted(config.LEVELS.items()))
        self._center_text(f"Pick a level: {levels}", self.small_font, DIM_TEXT, h * 0.54)
        hint = ("Your wrist moves the paddle (left/right, up/down).  C recalibrates.  Esc quits." if camera
                else "Arrow keys move the paddle (left/right, up/down).  Space = swing.  Esc quits.")
        self._center_text(hint, self.small_font, DIM_TEXT, h * 0.60)

    def _draw_camera(self, view):
        """Camera view (top right, under the scores)."""
        if view is None:
            return
        x, y = self.screen.get_width() - view.get_width() - 20, 92
        self.screen.blit(view, (x, y))
        pygame.draw.rect(self.screen, WALL, (x - 1, y - 1, view.get_width() + 2,
                                             view.get_height() + 2), 1)

    def _draw_paddle_messages(self, game: gs.GameState, src, camera_error):
        """Calibration / camera error text, or "no hand"."""
        h = self.screen.get_height()
        message = src.message or camera_error
        if message:
            for i, line in enumerate(message.split("\n")):   # 2nd line: e.g. why calibration was rejected
                self._center_text(line, self.font if i == 0 else self.small_font, CALIBRATE,
                                  h * 0.72 + i * 34)
        elif src.kind == "camera":
            # Small fixed badge (top left, under the level): how the hand is being found
            labels = {"wrist": "tracking: wrist", "hand": "tracking: fallback (knuckles)",
                      "elbow": "tracking: fallback (elbow)", "hold": "tracking: holding last position",
                      "card": "tracking: ignoring the hand at the card",
                      "lost": "tracking: LOST (paddle frozen)"}
            colors = {"wrist": (40, 140, 70), "hand": (170, 120, 20), "elbow": (170, 90, 20),
                      "hold": (170, 90, 20), "card": (50, 90, 170), "lost": (200, 50, 60)}
            text = self.small_font.render(f"  {labels.get(src.tracking, src.tracking)}  ", True,
                                          (255, 255, 255))
            box = text.get_rect(topleft=(24, 84))
            pygame.draw.rect(self.screen, colors.get(src.tracking, (90, 90, 90)), box.inflate(8, 8),
                             border_radius=6)
            self.screen.blit(text, box)

    def _center_text(self, text, font, color, y):
        surf = font.render(text, True, color)
        self.screen.blit(surf, surf.get_rect(center=(self.screen.get_width() / 2, y)))
