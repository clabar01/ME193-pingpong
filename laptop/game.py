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

import ball_log

import pygame

import config
import draw
import game_state as gs
import inputs
import mqtt_client
import sounds
import speech
import voice


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
        paddle_src = pose_input.CameraPaddle(pose, camera=camera, start_x=start_x, start_y=start_y,
                                             tags=tags)
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
                    age_s=now - last["arrival"], count=last["count"], via=last.get("via"))
    if swing_src.kind.endswith("keyboard"):
        key = swing_src.sources[-1].last_swing(now)
        if key is not None and (info["age_s"] is None or now - key.arrival < info["age_s"]):
            info.update(peak=None, peak_gyro=None, delay_ms=None, age_s=now - key.arrival,
                        count=info["count"])
    return info


def apply_actions(game, actions, paddle_ready: bool) -> list:
    """Apply actions from keyboard or tags: ("level" | "start" | "reset", value)
    only on the WAITING or GAME_OVER screen (a stray card mid-game can't
    change the level or wipe the record; "start" on GAME_OVER = play again,
    which GameState.start() refuses for the first GAME_OVER_SWING_IGNORE_S);
    ("slowmo" | "extra_life", None) power-up cards any time (GameState decides
    if they can be used now)."""
    events = []
    waiting = game.state in (gs.WAITING, gs.GAME_OVER)
    for action, value in actions:
        if action == "level" and waiting:
            game.set_level(value)
        elif action == "start" and waiting and paddle_ready:
            if value is not None:
                game.set_level(value)
            events += game.start()
        elif action == "reset" and waiting:
            events += game.reset_best()
        elif action == "slowmo":
            events += game.use_slowmo()
        elif action == "extra_life":
            events += game.use_extra_life()
        elif action == "smash":
            events += game.arm_smash()
        elif action == "pause":
            events += game.pause()
        elif action == "resume":
            events += game.resume()
        elif action == "pause_toggle":
            events += game.resume() if game.paused else game.pause()
    return events


# Voice command -> game action (see voice.py). Menu commands only act on the
# screens where they make sense; GameState refuses the rest at the wrong time.
VOICE_ACTIONS = {"smash": ("smash", None), "pause": ("pause", None), "resume": ("resume", None),
                 "level one": ("start", 0), "level two": ("start", 1), "level three": ("start", 2)}


def voice_outcome(command: str, game, events: list, paddle_ready: bool):
    """(short note, kind) for the mic after a voice command: kind "ok" (it
    worked), "info" (it will happen soon) or "no" (refused, and why)."""
    if command == "smash":
        if gs.EVENT_SMASH_ARMED in events:
            return ("smash: next ball" if game.smash_ready and not game.smash_armed else "smash: armed"), "ok"
        note = (game.card_note or ("",))[0]
        return ("smash: used" if "already used" in note else "smash: no rally"), "no"
    if command == "pause":
        if gs.EVENT_PAUSE in events:
            return "pause", "ok"
        if gs.EVENT_PAUSE_PENDING in events:
            return "pause: after this ball", "info"
        return "pause: no rally", "no"
    if command == "resume":
        return ("resume", "ok") if gs.EVENT_RESUME in events else ("resume: not paused", "no")
    # play again / level one-two-three: start a game
    if gs.EVENT_READY_COUNT in events:
        return command, "ok"
    if game.state not in (gs.WAITING, gs.GAME_OVER) or (command == "play again" and game.state != gs.GAME_OVER):
        return f"{command}: not now", "no"
    if game.state == gs.GAME_OVER and not game.can_play_again:
        return f"{command}: wait", "no"
    if not paddle_ready:
        return f"{command}: calibrate first", "no"
    return f"{command}: not now", "no"


def voice_actions(command: str, game) -> list:
    """The actions for a voice command ("score" is handled by game.py: it speaks)."""
    if command == "play again":        # = a swing on the GAME OVER screen
        return [("start", None)] if game.state == gs.GAME_OVER else []
    if command.startswith("level") and game.state not in (gs.WAITING, gs.GAME_OVER):
        return []
    return [VOICE_ACTIONS[command]] if command in VOICE_ACTIONS else []


def main():
    parser = argparse.ArgumentParser(description="ME193 virtual ping-pong")
    parser.add_argument("--input", choices=["keyboard", "camera"], default="keyboard",
                        help="what moves the paddle (default: keyboard)")
    parser.add_argument("--tags", action="store_true",
                        help="use AprilTags with the keyboard paddle "
                             "(always on with --input camera)")
    parser.add_argument("--no-imu", action="store_true",
                        help="ignore the paddle IMU: every ball counts as swung")
    parser.add_argument("--debug", action="store_true",
                        help="start with the debug overlay on (D toggles it)")
    parser.add_argument("--no-voice", action="store_true",
                        help="no voice commands (no microphone, no text-to-speech)")
    parser.add_argument("--no-publish", action="store_true",
                        help="never publish the record to the score topic (for testing)")
    args = parser.parse_args()

    pygame.init()
    screen = pygame.display.set_mode((config.WINDOW_WIDTH, config.WINDOW_HEIGHT))
    pygame.display.set_caption("ME193 Ping Pong")
    clock = pygame.time.Clock()

    game = gs.GameState()
    game.camera_mode = args.input == "camera"   # wider hit tolerance for wrist tracking
    log = ball_log.BallLog(mode=args.input)      # one CSV row per ball + summary at quit
    event_log = ball_log.EventLog()               # game events + sounds, for tracing
    renderer = draw.Renderer(screen, debug=args.debug or config.DEBUG_OVERLAY)   # D toggles debug

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
    key_src = inputs.KeyboardStart()       # Space / 1-2-3: always available as a fallback
    player = sounds.SoundPlayer()          # own thread; config.SOUND_MUTED turns it off
    sound_rules = sounds.SoundRules(game.best_streak)   # which sound when (see sounds.py)
    # Voice commands: Vosk on the mic in its own thread; it ignores the mic while
    # a game sound plays or the laptop speaks (text-to-speech in its own process)
    voice_on = config.VOICE_ENABLED and not args.no_voice
    speaker = speech.Speaker(enabled=voice_on)
    listener = voice.VoiceListener(enabled=voice_on, mute_sources=[player.voice_muted, lambda: speaker.busy])
    sound_notes = []                       # (time, "name: reason") shown on screen
    # ONE message at a time: the newest replaces the old (see draw.py)
    banner = None                          # (text, kind, start time); kind good / bad / power / neutral
    voice_note = None                      # (text, "ok" / "info" / "no", time) shown by the mic

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
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_d:
                    renderer.debug = not renderer.debug   # debug overlay on / off
                key_src.handle_event(event)
                if game.state == gs.PLAYING:
                    swing_src.handle_event(event)    # Space = swing (keyboard paddle)
                if game.state in (gs.WAITING, gs.GAME_OVER):
                    paddle_src.handle_event(event)   # e.g. C = recalibrate the camera

            actions = key_src.poll()
            voice_commands = listener.poll()                # handled one by one below
            for _, line in listener.poll_log():
                print(line)
                event_log.write(line)
            if any(a == "score" for a, _ in actions):      # "score": say it out loud
                count = game.last_rally if game.state == gs.GAME_OVER else game.streak
                text = f"{count} volleys. Best {game.best_streak}."
                speaker.say(text)
                event_log.write(f"SPEAK {text!r}")
                actions = [a for a in actions if a[0] != "score"]
            menu = game.state in (gs.WAITING, gs.GAME_OVER)   # level / reset tags count here
            if tag_src is not None:
                # On GAME_OVER a level tag's hold only counts once playing again is allowed
                can_start = (paddle_src.ready and (game.state == gs.WAITING or game.can_play_again))
                tag_src.update(can_start=can_start, waiting=menu)
                actions += tag_src.poll()
            # GAME OVER: a real paddle swing (IMU) after the ignore time plays
            # again at the same level. (Not with --no-imu, where every moment
            # counts as a swing; Space plays again via the keyboard actions.)
            if game.can_play_again and "imu" in swing_src.kind:
                swing = swing_src.last_swing(now)
                if swing is not None and swing.arrival >= game.game_over_at + config.GAME_OVER_SWING_IGNORE_S:
                    actions.append(("start", None))
            streak_before = game.streak            # (a miss resets it; the sound rules need it)
            state_before = game.state
            events = apply_actions(game, actions, paddle_src.ready)
            # Voice commands, one at a time so each gets its own note by the mic
            for command, heard, _ in voice_commands:
                game.note_voice(command)                    # (CSV: commands before each ball)
                if command == "score":                      # say it out loud
                    count = game.last_rally if game.state == gs.GAME_OVER else game.streak
                    text = f"{count} volleys. Best {game.best_streak}."
                    speaker.say(text)
                    event_log.write(f"SPEAK {text!r}")
                    voice_note = ("score", "ok", now)
                    continue
                ev = apply_actions(game, voice_actions(command, game), paddle_src.ready)
                events += ev
                text, kind = voice_outcome(command, game, ev, paddle_src.ready)
                voice_note = (text, kind, now)
                if kind != "ok":
                    event_log.write(f"VOICE {command!r} not done: {text}")
            for command, why, _ in listener.poll_rejected():   # heard, but not accepted
                voice_note = (f"{command}: {why}", "no", now)

            paddle_src.update(dt)
            game.set_paddle(paddle_src.x, paddle_src.y)
            game.tracking_ok = paddle_src.has_hand
            game.tracking = paddle_src.tracking
            # Swing timing window for the next contact: centered while IMU swings
            # come through the local broker (or there's no network delay at all),
            # shifted late while they come over hivemq (see the hit rule).
            slow = "imu" in swing_src.kind and mqtt is not None and not mqtt.local_ok
            game.swing_window = config.SWING_WINDOW_SLOW if slow else config.SWING_WINDOW_FAST
            rejected = mqtt.last_rejected if mqtt is not None and swing_src.kind != "always" else None
            events += game.update(dt, now, swing_src.last_swing(now), rejected)
            for record in game.take_results():   # (held back during the extra-life countdown)
                log.add(record)
            if gs.EVENT_LIFE_OFFER in events and tag_src is not None:
                tag_src.rearm(config.TAG_EXTRA_LIFE)   # card already up when I missed: let it count
            # The one message (later lines win if two happen in the same frame)
            if gs.EVENT_GO in events:
                banner = ("GO!", "good", now)
            if gs.EVENT_SLOWMO in events:
                banner = ("SLOW-MO!", "power", now)
            if gs.EVENT_LIFE_USED in events:
                banner = ("EXTRA LIFE!", "power", now)
            if gs.EVENT_SMASH_ARMED in events:
                banner = ("SMASH ARMED", "power", now)
            if gs.EVENT_SMASH_HIT in events:
                banner = ("SMASH!", "power", now)
            if gs.EVENT_CARD_REFUSED in events and game.card_note:
                used = "already used" in game.card_note[0]
                banner = ("ALREADY USED" if used else "NOT NOW", "neutral", now)
            # A fresh miss: one or two words ("TOO LATE", "MISSED", "SWING!") on
            # their own, before the EXTRA LIFE countdown or GAME OVER screen.
            # (Not again when the countdown runs out: that miss was shown already.)
            if gs.EVENT_LIFE_OFFER in events or (gs.EVENT_GAME_OVER in events and state_before != gs.EXTRA_LIFE):
                banner = (draw.short_miss(game.miss_text), "bad", now)
            shown = [e for e in events if e != gs.EVENT_BOUNCE]   # bounces: too chatty
            if shown:
                line = f"streak={game.streak} best={game.best_streak} events={shown}"
                print(line)
                event_log.write(line)
            if gs.EVENT_GAME_OVER in events:
                line = (f"GAME OVER: final score {game.last_rally} volleys, level {game.level}, "
                        f"best {game.best_streak}" + (", NEW HIGH SCORE" if game.new_high_score else ""))
                print(line)
                event_log.write(line)
            # Sounds: SoundRules decides (rules at the top of sounds.py); every
            # sound is printed, logged, and shown on screen with its reason.
            for name, reason in sound_rules.decide(events, game, streak_before):
                player.play(name)
                line = f"SOUND {name}: {reason}"
                event_log.write(line)
                if name == "record":
                    banner = ("HIGH SCORE!", "good", now)   # the banner goes with the chime (same rule)
                if name != "tick":         # ticks: in the log only (two per shot)
                    print(line)
                    sound_notes.append((now, f"{name}: {reason}"))
            sound_notes = [(t, s) for t, s in sound_notes if now - t < config.SOUND_SHOW_S][-3:]
            if mqtt is not None:
                mqtt.update(game)   # record (when it goes up) + game state at 10 Hz

            # Camera view with each source's extras drawn on it
            view = camera.preview_surface() if camera is not None else None
            if view is not None:
                paddle_src.draw_on_preview(view, debug=renderer.debug)
                if tag_src is not None:
                    tag_src.draw_on_preview(view)
            renderer.draw(game, paddle_src, view, camera_error=getattr(camera, "error", None),
                          mqtt_status=None if mqtt is None else (mqtt.connected, mqtt.status()),
                          swing_info=swing_display(swing_src, mqtt, now),
                          sound_notes=[s for _, s in sound_notes],
                          banner=None if banner is None else (banner[0], banner[1], now - banner[2]),
                          now=now, play_again=("imu" in swing_src.kind, tag_src is not None),
                          voice=(listener.status, voice_note))
            pygame.display.flip()
    finally:
        game.flush_results()
        while game.ball_results:
            log.add(game.ball_results.pop(0))
        print("\n" + log.summary())
        if mqtt is not None:
            print(mqtt.summary())
        log.close()
        event_log.close()
        if camera is not None:
            camera.stop()
        if mqtt is not None:
            mqtt.stop()
        listener.stop()
        speaker.stop()
        player.stop()
        pygame.quit()


if __name__ == "__main__":
    main()
