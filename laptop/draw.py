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
STATE_COLORS = {gs.WAITING: (120, 180, 255), gs.PLAYING: (120, 220, 140), gs.MISS: (255, 110, 110)}


class Renderer:
    def __init__(self, screen: pygame.Surface):
        self.screen = screen
        self.font = pygame.font.SysFont(None, 36)
        self.big_font = pygame.font.SysFont(None, 96)
        self.small_font = pygame.font.SysFont(None, 26)

    def draw(self, game: gs.GameState):
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
            self._draw_waiting(game)
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

    def _draw_waiting(self, game: gs.GameState):
        h = self.screen.get_height()
        self._center_text("PING PONG", self.big_font, TEXT, h * 0.32)
        self._center_text("Press SPACE to start", self.font, STATE_COLORS[gs.WAITING], h * 0.46)
        levels = "   ".join(f"[{n}] {v['name']}" for n, v in sorted(config.LEVELS.items()))
        self._center_text(f"Pick a level: {levels}", self.small_font, DIM_TEXT, h * 0.54)
        self._center_text("Left / Right arrows move the paddle.  Esc quits.",
                          self.small_font, DIM_TEXT, h * 0.60)

    def _center_text(self, text, font, color, y):
        surf = font.render(text, True, color)
        self.screen.blit(surf, surf.get_rect(center=(self.screen.get_width() / 2, y)))
