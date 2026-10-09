"""Virtual ping-pong.

Run from the repo root:
    python laptop/game.py                   # keyboard paddle (default, always works)
    python laptop/game.py --input camera    # wrist tracking + AprilTags (one webcam)
    python laptop/game.py --tags            # keyboard paddle + AprilTags

Wires the pieces together: input sources -> GameState (rules) -> Renderer.
To add the IMU later, swap the swing source below; the rest of the loop stays
the same.
"""
import argparse
import time

import pygame

import config
import draw
import game_state as gs
import inputs
import mqtt_client


def make_sources(args, start_x: float):
    """Build the camera (if needed) and the paddle / control sources.

    The camera is opened once; pose and AprilTag detection share its thread.
    MediaPipe is imported only when needed because it's slow to load.
    """
    use_pose = args.input == "camera"
    use_tags = use_pose or args.tags
    camera = None
    processors = []
    pose = tags = None
    if use_pose:
        import pose_input
        pose = pose_input.PoseProcessor()
        processors.append(pose)
    if use_tags:
        import apriltag_input
        tags = apriltag_input.TagProcessor()
        processors.append(tags)
    if processors:
        import camera as camera_mod
        camera = camera_mod.Camera(processors)
        camera.start()

    if pose is not None:
        paddle_src = pose_input.CameraPaddle(pose, camera=camera, start_x=start_x)
    else:
        paddle_src = inputs.KeyboardPaddle(start_x=start_x)
    tag_src = apriltag_input.TagStart(tags) if tags is not None else None
    return camera, paddle_src, tag_src


def apply_actions(game, actions, paddle_ready: bool) -> list:
    """Apply ("level" | "start" | "reset", value) actions from keyboard or tags."""
    events = []
    for action, value in actions:
        if action == "level" and game.state == gs.WAITING:
            game.set_level(value)
        elif action == "start" and game.state == gs.WAITING and paddle_ready:
            if value is not None:
                game.set_level(value)
            events += game.start()
        elif action == "reset":
            events += game.reset_best()
    return events


def main():
    parser = argparse.ArgumentParser(description="ME193 virtual ping-pong")
    parser.add_argument("--input", choices=["keyboard", "camera"], default="keyboard",
                        help="what moves the paddle (default: keyboard)")
    parser.add_argument("--tags", action="store_true",
                        help="use AprilTags with the keyboard paddle "
                             "(always on with --input camera)")
    args = parser.parse_args()

    pygame.init()
    screen = pygame.display.set_mode((config.WINDOW_WIDTH, config.WINDOW_HEIGHT))
    pygame.display.set_caption("ME193 Ping Pong")
    clock = pygame.time.Clock()

    game = gs.GameState()
    renderer = draw.Renderer(screen)

    camera, paddle_src, tag_src = make_sources(args, game.paddle.x)
    swing_src = inputs.AlwaysSwing()       # IMU replaces this later
    # MQTT runs in its own background thread; the game never waits for it
    mqtt = mqtt_client.GameMqtt() if config.MQTT_ENABLED else None
    key_src = inputs.KeyboardStart()       # Space / 0-2: always available as a fallback

    running = True
    try:
        while running:
            dt = clock.tick(config.FPS) / 1000.0
            now = time.monotonic()

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False
                key_src.handle_event(event)
                if game.state == gs.WAITING:
                    paddle_src.handle_event(event)   # e.g. C = recalibrate the camera

            actions = key_src.poll()
            if tag_src is not None:
                tag_src.update(can_start=game.state == gs.WAITING and paddle_src.ready)
                actions += tag_src.poll()
            events = apply_actions(game, actions, paddle_src.ready)

            paddle_src.update(dt)
            game.set_paddle_x(paddle_src.x)
            events += game.update(dt, swing_ok=swing_src.swing_ok(now))
            if events:   # for sounds later
                print(f"streak={game.streak} best={game.best_streak} events={events}")
            if mqtt is not None:
                mqtt.update(game)   # record (when it goes up) + game state at 10 Hz

            # Camera view with each source's extras drawn on it
            view = camera.preview_surface() if camera is not None else None
            if view is not None:
                paddle_src.draw_on_preview(view)
                if tag_src is not None:
                    tag_src.draw_on_preview(view)
            renderer.draw(game, paddle_src, view, camera_error=getattr(camera, "error", None),
                          mqtt_connected=None if mqtt is None else mqtt.connected)
            pygame.display.flip()
    finally:
        if camera is not None:
            camera.stop()
        if mqtt is not None:
            mqtt.stop()
        pygame.quit()


if __name__ == "__main__":
    main()
