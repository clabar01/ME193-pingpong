"""Virtual ping-pong, keyboard version.

Run from the repo root:  python laptop/game.py

Wires the pieces together: input sources -> GameState (rules) -> Renderer.
To add the camera or IMU later, swap the source objects below; the rest of
the loop stays the same.
"""
import time

import pygame

import config
import draw
import game_state as gs
import inputs


def main():
    pygame.init()
    screen = pygame.display.set_mode((config.WINDOW_WIDTH, config.WINDOW_HEIGHT))
    pygame.display.set_caption("ME193 Ping Pong")
    clock = pygame.time.Clock()

    game = gs.GameState()
    renderer = draw.Renderer(screen)

    # Input sources (keyboard for now; camera / IMU / AprilTag plug in here later)
    paddle_src = inputs.KeyboardPaddle(start_x=game.paddle.x)
    swing_src = inputs.AlwaysSwing()
    start_src = inputs.KeyboardStart()

    running = True
    while running:
        dt = clock.tick(config.FPS) / 1000.0
        now = time.monotonic()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False
            start_src.handle_event(event)

        events = []   # this frame's game events (EVENT_*), for sounds / MQTT later
        if game.state == gs.WAITING:
            game.set_level(start_src.level)
            level = start_src.poll()
            if level is not None:
                game.set_level(level)
                events += game.start()
        else:
            start_src.poll()   # ignore Space while playing

        paddle_src.update(dt)
        game.set_paddle_x(paddle_src.x)
        events += game.update(dt, swing_ok=swing_src.swing_ok(now))
        if events:
            print(f"streak={game.streak} best={game.best_streak} events={events}")

        renderer.draw(game)
        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()
