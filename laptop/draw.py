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
STATE_COLORS = {gs.WAITING: (120, 180, 255), gs.PLAYING: (120, 220, 140),
                gs.MISS: (255, 110, 110), gs.POINT: (120, 220, 140)}


def P(x, y, z):
    """World (m) -> screen point (ints) for pygame."""
    sx, sy = view3d.project(x, y, z)
    return round(sx), round(sy)


class Renderer:
    def __init__(self, screen: pygame.Surface):
        self.screen = screen
        self.font = pygame.font.SysFont(None, 36)
        self.big_font = pygame.font.SysFont(None, 96)
        self.small_font = pygame.font.SysFont(None, 26)

    def draw(self, game: gs.GameState, paddle_src=None, camera_view=None, camera_error=None,
             mqtt_status=None, swing_info=None):
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
        if info["peak"] is not None:
            gyro = f"  {info['peak_gyro']:.0f} dps" if info["peak_gyro"] is not None else ""
            lines.append((f"swing #{info['count']}: {info['peak']:.2f} g{gyro}",
                          CALIBRATE if flash else TEXT))
            if info["delay_ms"] is not None and -2000 < info["delay_ms"] < 10000:
                lines.append((f"delay {info['delay_ms']:.0f} ms (board -> laptop)", TEXT))
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
                text, color = f"MQTT: connected  |  {detail}", STATE_COLORS[gs.PLAYING]
            else:
                text, color = f"MQTT: disconnected (retrying)  |  {detail}", STATE_COLORS[gs.MISS]
        surf = self.small_font.render(text, True, color)
        self.screen.blit(surf, (20, self.screen.get_height() - surf.get_height() - 12))

    def _draw_game(self, game: gs.GameState, paddle_src, tags_on: bool):
        h = self.screen.get_height()
        self._draw_scene(game)
        self._draw_hud(game)
        if game.state == gs.WAITING:
            self._draw_waiting(game, paddle_src, tags_on)
        elif game.state == gs.MISS:
            self._center_text("MISS", self.big_font, STATE_COLORS[gs.MISS], h * 0.30)
        elif game.state == gs.POINT:
            self._center_text("POINT!  opponent missed", self.font, STATE_COLORS[gs.POINT], h * 0.30)
            self._center_text("your streak continues", self.small_font, DIM_TEXT, h * 0.30 + 32)

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
        pygame.draw.circle(self.screen, BALL, (sx, sy), r)
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
        # Hit zone for this level: the ball's center must be inside this ring at contact
        reach = config.PADDLE_RADIUS_M + config.BALL_RADIUS_M + game.params["hit_tolerance"]
        pygame.draw.circle(self.screen, HIT_RING, P(p.x, p.y, z), view3d.size(reach, z), 1)

    def _draw_hud(self, game: gs.GameState):
        state = self.font.render(game.state, True, STATE_COLORS[game.state])
        self.screen.blit(state, (20, 16))
        level = config.LEVELS[game.level]
        lvl = self.small_font.render(f"Level {game.level}: {level['name']}", True, DIM_TEXT)
        self.screen.blit(lvl, (20, 52))

        w = self.screen.get_width()
        streak = self.font.render(f"Streak: {game.streak}", True, TEXT)
        best = self.font.render(f"Best: {game.best_streak}", True, TEXT)
        self.screen.blit(streak, (w - streak.get_width() - 20, 16))
        self.screen.blit(best, (w - best.get_width() - 20, 52))

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
            self._center_text(message, self.font, CALIBRATE, h * 0.72)
        elif not src.has_hand:
            # Paddle is frozen until the wrist is seen again
            p = game.paddle
            _, y = P(p.x, p.y + config.PADDLE_RADIUS_M * 1.4, config.MY_HIT_Z)
            self._center_text("no hand", self.font, STATE_COLORS[gs.MISS], y)

    def _center_text(self, text, font, color, y):
        surf = font.render(text, True, color)
        self.screen.blit(surf, surf.get_rect(center=(self.screen.get_width() / 2, y)))
