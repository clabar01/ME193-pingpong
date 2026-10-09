"""Drawing only: turns a GameState into pixels. No game rules in here."""
import pygame

import config
import game_state as gs

BACKGROUND = (18, 24, 38)
WALL = (70, 80, 100)
BALL = (255, 255, 255)
PADDLE = (240, 120, 60)
HIT_ZONE = (240, 120, 60, 60)   # translucent band showing where hits count
TEXT = (230, 230, 230)
DIM_TEXT = (150, 160, 180)
CALIBRATE = (255, 210, 90)
STATE_COLORS = {gs.WAITING: (120, 180, 255), gs.PLAYING: (120, 220, 140), gs.MISS: (255, 110, 110)}


class Renderer:
    def __init__(self, screen: pygame.Surface):
        self.screen = screen
        self.font = pygame.font.SysFont(None, 36)
        self.big_font = pygame.font.SysFont(None, 96)
        self.small_font = pygame.font.SysFont(None, 26)

    def draw(self, game: gs.GameState, paddle_src=None, camera_view=None, camera_error=None,
             mqtt_connected=None):
        """Draw one frame.

        paddle_src (optional) adds its messages ("no hand", calibration).
        camera_view: Surface with the camera picture and overlays, or None.
        camera_error: text if the camera failed, shown instead of the view.
        mqtt_connected: True / False for the status line, None = MQTT off.
        """
        tags_on = camera_view is not None or camera_error is not None
        self._draw_game(game, paddle_src, tags_on)
        self._draw_camera(camera_view)
        if paddle_src is not None:
            self._draw_paddle_messages(game, paddle_src, camera_error)
        self._draw_mqtt(mqtt_connected)

    def _draw_mqtt(self, connected):
        """MQTT status, bottom left."""
        if connected is None:
            text, color = "MQTT: off", DIM_TEXT
        elif connected:
            text, color = "MQTT: connected", STATE_COLORS[gs.PLAYING]
        else:
            text, color = "MQTT: disconnected (retrying)", STATE_COLORS[gs.MISS]
        surf = self.small_font.render(text, True, color)
        self.screen.blit(surf, (20, self.screen.get_height() - surf.get_height() - 12))

    def _draw_game(self, game: gs.GameState, paddle_src, tags_on: bool):
        s = self.screen
        s.fill(BACKGROUND)
        w, h = s.get_size()

        # Side walls and far wall (the bottom is open: that's the player's side)
        pygame.draw.line(s, WALL, (1, 0), (1, h), 3)
        pygame.draw.line(s, WALL, (w - 2, 0), (w - 2, h), 3)
        pygame.draw.line(s, WALL, (0, 1), (w, 1), 3)

        self._draw_paddle(game.paddle)
        if game.state != gs.WAITING:
            b = game.ball
            pygame.draw.circle(s, BALL, (round(b.x), round(b.y)), round(b.radius))

        self._draw_hud(game)
        if game.state == gs.WAITING:
            self._draw_waiting(game, paddle_src, tags_on)
        elif game.state == gs.MISS:
            self._center_text("MISS", self.big_font, STATE_COLORS[gs.MISS], h * 0.45)

    def _draw_paddle(self, p: gs.Paddle):
        zone = pygame.Surface((p.hit_zone, config.PADDLE_HEIGHT * 3), pygame.SRCALPHA)
        zone.fill(HIT_ZONE)
        self.screen.blit(zone, (p.x - p.hit_zone / 2, p.y - config.PADDLE_HEIGHT))
        pygame.draw.rect(self.screen, PADDLE,
                         pygame.Rect(p.x - p.width / 2, p.y, p.width, config.PADDLE_HEIGHT),
                         border_radius=6)

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
        self._center_text("PING PONG", self.big_font, TEXT, h * 0.32)
        if ready:
            start = ("Hold up a level tag (0-2) for 1 s, or press SPACE" if tags_on
                     else "Press SPACE to start")
            self._center_text(start, self.font, STATE_COLORS[gs.WAITING], h * 0.46)
        levels = "   ".join(f"[{n}] {v['name']}" for n, v in sorted(config.LEVELS.items()))
        self._center_text(f"Pick a level: {levels}", self.small_font, DIM_TEXT, h * 0.54)
        hint = ("Your wrist moves the paddle.  C recalibrates.  Esc quits." if camera
                else "Left / Right arrows move the paddle.  Esc quits.")
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
            self._center_text("no hand", self.font, STATE_COLORS[gs.MISS],
                              game.paddle.y - 50)

    def _center_text(self, text, font, color, y):
        surf = font.render(text, True, color)
        self.screen.blit(surf, surf.get_rect(center=(self.screen.get_width() / 2, y)))
