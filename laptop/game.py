"""Virtual ping-pong.

Run from the repo root:
    python laptop/game.py                   # keyboard paddle (default, always works)
    python laptop/game.py --input camera    # wrist tracking + AprilTags (one webcam)
    python laptop/game.py --tags            # keyboard paddle + AprilTags
    add --no-imu to ignore the paddle's swing sensor (every ball counts as swung)
    add --no-publish to any of them: never publish the record (for testing)

Swings: the paddle IMU (over MQTT) in every mode; with the keyboard paddle,
Space is also a swing. The record on MQTT is only published with
--input camera AND the IMU active (see mqtt_client.py).

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


def make_sources(args, start_x: float, start_y: float):
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
        paddle_src = pose_input.CameraPaddle(pose, camera=camera, start_x=start_x, start_y=start_y)
    else:
        paddle_src = inputs.KeyboardPaddle(start_x=start_x, start_y=start_y)
    tag_src = apriltag_input.TagStart(tags) if tags is not None else None
    return camera, paddle_src, tag_src


def swing_display(swing_src, mqtt, now: float) -> dict:
    """What the swing meter shows (see Renderer._draw_swing_meter)."""
    info = {"mode": swing_src.kind, "peak": None, "peak_gyro": None, "delay_ms": None,
            "age_s": None, "count": 0}
    last = mqtt.last_swing_info if mqtt is not None and swing_src.kind != "always" else None
    if last is not None:
        info.update(peak=last["peak"], peak_gyro=last["peak_gyro"], delay_ms=last["delay_ms"],
                    age_s=now - last["arrival"], count=last["count"])
    if swing_src.kind.endswith("keyboard"):
        key = swing_src.sources[-1].last_swing(now)
        if key is not None and (info["age_s"] is None or now - key.arrival < info["age_s"]):
            info.update(peak=None, peak_gyro=None, delay_ms=None, age_s=now - key.arrival,
                        count=info["count"])
    return info


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
    parser.add_argument("--no-imu", action="store_true",
                        help="ignore the paddle IMU: every ball counts as swung")
    parser.add_argument("--no-publish", action="store_true",
                        help="never publish the record to the score topic (for testing)")
    args = parser.parse_args()

    pygame.init()
    screen = pygame.display.set_mode((config.WINDOW_WIDTH, config.WINDOW_HEIGHT))
    pygame.display.set_caption("ME193 Ping Pong")
    clock = pygame.time.Clock()

    game = gs.GameState()
    renderer = draw.Renderer(screen)

    camera, paddle_src, tag_src = make_sources(args, game.paddle.x, game.paddle.y)
    # IMU swings arrive over MQTT, so the IMU needs MQTT on.
    imu_active = config.MQTT_ENABLED and not args.no_imu
    # Only real play may publish the record: camera paddle AND IMU swings.
    # Keyboard / --tags / --no-imu / --no-publish still load and show it.
    publish_score = args.input == "camera" and imu_active and not args.no_publish
    why_not = ("--no-publish" if args.no_publish else "--no-imu" if args.no_imu
               else "keyboard paddle" if args.input != "camera" else "IMU off")
    # MQTT runs in its own background thread; the game never waits for it
    mqtt = mqtt_client.GameMqtt(publish_score, why_not) if config.MQTT_ENABLED else None

    # Swing source (see the hit rule in game_state.py)
    if not imu_active:
        swing_src = inputs.AlwaysSwing()                      # every ball counts as swung
    elif args.input == "camera":
        swing_src = inputs.ImuSwing(mqtt)                     # real swings only
    else:
        swing_src = inputs.LatestSwing(inputs.ImuSwing(mqtt), inputs.KeyboardSwing())
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
                if game.state == gs.PLAYING:
                    swing_src.handle_event(event)    # Space = swing (keyboard paddle)
                if game.state == gs.WAITING:
                    paddle_src.handle_event(event)   # e.g. C = recalibrate the camera

            actions = key_src.poll()
            if tag_src is not None:
                tag_src.update(can_start=game.state == gs.WAITING and paddle_src.ready)
                actions += tag_src.poll()
            events = apply_actions(game, actions, paddle_src.ready)

            paddle_src.update(dt)
            game.set_paddle(paddle_src.x, paddle_src.y)
            events += game.update(dt, now, swing_src.last_swing(now))
            shown = [e for e in events if e != gs.EVENT_BOUNCE]   # bounces: too chatty
            if shown:   # for sounds later
                print(f"streak={game.streak} best={game.best_streak} events={shown}")
            if mqtt is not None:
                mqtt.update(game)   # record (when it goes up) + game state at 10 Hz

            # Camera view with each source's extras drawn on it
            view = camera.preview_surface() if camera is not None else None
            if view is not None:
                paddle_src.draw_on_preview(view)
                if tag_src is not None:
                    tag_src.draw_on_preview(view)
            renderer.draw(game, paddle_src, view, camera_error=getattr(camera, "error", None),
                          mqtt_status=None if mqtt is None else (mqtt.connected, mqtt.status()),
                          swing_info=swing_display(swing_src, mqtt, now))
            pygame.display.flip()
    finally:
        if camera is not None:
            camera.stop()
        if mqtt is not None:
            mqtt.stop()
        pygame.quit()


if __name__ == "__main__":
    main()
