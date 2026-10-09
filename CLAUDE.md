# ME193 Midterm — Virtual Ping-Pong

Cecilia LaBarge's ME193 robotics midterm. A virtual ball moves on the laptop screen (pygame); she hits it back with a **real paddle**. The laptop tracks where the paddle is (webcam + MediaPipe), and an accelerometer on the paddle tells it *when* she swung (Arduino UNO Q → MQTT).

## How the game works

1. **Start / level select:** holding up an AprilTag starts the game. The tag ID picks the level, which sets ball speed and hit-zone size.
2. **Paddle position:** MediaPipe Pose tracks the wrist in the laptop webcam feed; the wrist position maps to the on-screen paddle.
3. **Swing detection:** an accelerometer taped to the paddle is read by the Arduino UNO Q, which detects swings and publishes swing events over MQTT.
4. **Hit rule:** a hit counts only if **both** are true:
   - the paddle (wrist) is inside the hit zone when the ball arrives, **and**
   - a swing event arrived within a short timing window around that moment.
   Otherwise it's a miss and the rally ends.
5. **Score:** count continuous hits in a rally. When the all-time record is beaten, publish it (see MQTT below).
6. **LED matrix:** the UNO Q's LED matrix shows the ball as a single dot, mirroring the on-screen ball.
7. **Sounds:** pyAudio for hit/miss/start sounds.
8. **New feature (not done in class) — UNDECIDED:** either voice commands (Vosk) or an AI commentator (LLM + text-to-speech). Ask before building either.

## MQTT topics

| Topic | Direction | Content |
|---|---|---|
| `ME193/Rogers/CeciLaBarge` | laptop → broker | **Only** the record number of continuous hits, as a **float** (e.g. `12.0`), **retained**, published **every time the record changes**. Nothing else is ever published here — no debug, no state, no strings. |
| `ME193/CeciLaBarge/imu` | UNO Q → laptop | Swing events from the accelerometer |
| `ME193/CeciLaBarge/game` | laptop → UNO Q | Ball position + game state for the LED matrix dot |
| `ME193/CeciLaBarge/imu/rejected` | UNO Q → laptop | Tuning only: moves that failed the swing checks (game ignores it) |
| `ME193/CeciLaBarge/imu/raw` | UNO Q → laptop | Only with `DEBUG = True` on the board: raw samples for labeling |

Broker address, topics, and credentials are configured in `config.py` / `board_secrets.py` — never hard-coded elsewhere.

## Rules for working on this project

- **Python on the laptop.** (UNO Q side uses whatever the board needs — Arduino sketch and/or its Python app.)
- **All tunable values live in one `config.py`:** thresholds, timing window, ball speeds, hit-zone sizes per level, AprilTag ID → level map, MQTT broker/port/topics, camera index, screen size, etc. No magic numbers scattered in other files.
- **Threads:** camera (MediaPipe + AprilTag), MQTT, and audio each run in **their own thread** so the pygame loop never blocks. Threads share state through thread-safe structures (locks / queues / latest-value holders); the game loop only reads the latest values.
- **Keyboard fallback always works:** a keyboard paddle mode (arrow keys move the paddle, Space = swing while playing) must remain available so the game is playable/demoable with no camera or no UNO Q (`--no-imu` makes every ball count as swung).
- **Never commit secrets:** `board_secrets.py` and any API keys (e.g. LLM / TTS keys) must be in `.gitignore`. Provide a `board_secrets_example.py` template with placeholder values instead.
- **Explain the logic in comments:** the hit rule and the swing-detection logic must have clear, plain-language comments (what is checked, why, which config values control it) — Cecilia quotes these in her write-up.
- **After every step, give exact testing instructions:** commands to run, what to do physically (e.g. "hold tag 3 up to the camera"), and what she should see/hear if it works.
- Build in small steps; don't write code beyond the current step.

## Reuse from past projects

Past repos (only read them once Cecilia grants access):
- MediaPipe pose tracking — "Pose race": github.com/clabar01/ME193-car
- AprilTag detection — "AprilTag parking": github.com/clabar01/ME193-3-AI-in-Mobile-Robots, folder `AprilTag Parking/` (uses OpenCV `cv2.aruco` with `DICT_APRILTAG_36h11`, not pupil-apriltags)
- pyAudio sounds — "Whistling World Cup": github.com/clabar01/Lego/tree/main/world_cup
- MQTT + UNO Q LED dot — "door-to-door minifig YOLO": github.com/clabar01/mini-fig-yolo

Prefer adapting her existing code/patterns from these over writing new approaches, so the project stays consistent with what she's done in class.

## Environment notes

- Laptop: Apple Silicon Mac, Python 3.14, venv at `venv/` (git-ignored). No Homebrew installed as of 2026-10-08.
- `pygame-ce` instead of `pygame` (no pygame build for 3.14); code still does `import pygame`.
- MediaPipe 1.1.0 has **no `mp.solutions.pose`**; use the Tasks API (`mp.tasks.vision.PoseLandmarker` + a downloaded `.task` model). Code from the Pose race repo must be adapted.
- Only `opencv-contrib-python` (MediaPipe's dependency); never also install `opencv-python`.
- OpenCV 5 and pygame both bundle SDL2 → macOS prints "Class SDL... is implemented in both" warnings. Don't use `cv2.imshow`; draw camera previews in pygame.
- pyaudio needs PortAudio. On this Mac (no Homebrew) PortAudio v19.7.0 was built from source as a static, arm64-only library into `~/.local/portaudio` (headers copied manually from the source `include/`, including `pa_mac_core.h`). pyaudio 0.2.14 was installed with:
  `ARCHFLAGS="-arch arm64" CFLAGS="-I$HOME/.local/portaudio/include" LDFLAGS="-L$HOME/.local/portaudio/lib -framework CoreAudio -framework AudioToolbox -framework AudioUnit -framework CoreFoundation -framework CoreServices" pip install pyaudio`
  Recreating the venv means re-running that command; a plain `pip install -r requirements.txt` fails on pyaudio.

## Two-folder setup

- **`~/ME193-pingpong`** (this repo, github.com/clabar01/ME193-pingpong): laptop code — game, camera, MQTT, sounds.
- **`~/ArduinoApps`** (github.com/clabar01/ArduinoApps): board code. **All UNO Q apps for this project go there** (planned: `paddle_imu` for swing detection, `pong_display` for the LED matrix ball), never in this repo.
- Deploy board apps with `python3 tools/deploy.py <app>` from `~/ArduinoApps`, or Cmd-Shift-B → "Run on UNO Q". **Cmd-Shift-B only works when `~/ArduinoApps` is the folder open in VS Code** (the task uses `${workspaceFolder}/tools/deploy.py`). See that repo's CLAUDE.md for its workflow rules.

## Board settings: one secrets file

- The **only** board settings file is **`~/ArduinoApps/tools/board_secrets.py`** (git-ignored there): `HOSTS`, `USER`, `BOARD_NAME`, `BOARD_IP`.
- `tools/advertise_board.py` in this repo reads `BOARD_NAME`/`BOARD_IP` from it by path; this repo has no `board_secrets.py` of its own (`tools/board_secrets_example.py` is documentation only). Don't create a second copy.
- Switching home ↔ campus: change **`BOARD_IP`** there; nothing else. `HOSTS` already lists both: `["10.247.137.47" (Tufts_Robot, campus), "192.168.1.185" (home), "AirFour.local"]`.

## UNO Q notes

- Board: AirFour, user `arduino`, home IP 192.168.1.185, campus (Tufts_Robot) IP 10.247.137.47 (confirmed 2026-10-09). Campus WiFi latency varies (35–180 ms ping); a single deploy.py "Couldn't reach the board" can be a timeout, so retry once before assuming the IP changed.
- App Python runs in a Docker container on its own network: `wlan0` is not visible and socket tricks return a 172.x address. App Lab sets `HOST_IP` at app start (fixed afterwards). For a live value, the board's crontab runs `show_ip/write_host_ip.sh` every minute, writing the wlan0 IP to `~/ArduinoApps/show_ip/host_ip.txt` (`/app/host_ip.txt` inside the show_ip container; empty = no WiFi).
- LED matrix pattern (from `minifig_tracker`): Python owns the logic and calls Bridge providers in the sketch (`show_dot(col,row)`, `clear_matrix()`); the sketch guards matrix writes with a `K_MUTEX`. Matrix is 13 cols × 8 rows. For text, include `ArduinoGraphics.h` before `Arduino_LED_Matrix.h` and add `ArduinoGraphics (1.1.4)` to `sketch.yaml` (see `show_ip`).
- **IMU (`~/ArduinoApps/paddle_imu`, step 1 done 2026-10-09):** MPU6050 (GY-521) on the header SDA/SCL = `Wire` (i2c2), address 0x68, WHO_AM_I 0x68 (genuine). UNO Q buses: `Wire`=i2c2 header, `Wire1`=i2c4 Qwiic, `Wire2`=i2c3 A4/A5. Read in the **sketch** (raw registers, no library) at 100 Hz, ±8 g, ±2000 °/s, DLPF 44 Hz; each sample goes to Python with `Bridge.notify("imu_sample", t_ms, ax, ay, az, gx, gy, gz)`; status/I2C scan via `Bridge.notify("imu_status", text)`. Python logs every 10th sample. At rest |a| ≈ 0.97 g, gyro bias ≈ 1–2 °/s. `arduino-app-cli app logs <app>` shows only the last 100 lines; use `--tail 100000` to see startup messages.
- **Swing events (paddle_imu, deployed 2026-10-09):** detection in `paddle_imu/python/swing_detector.py` (pure Python, unit-tested on laptop): candidate when |a| − 1 g > 2.0 g; swing only if peak accel ≥ 2.2 g **and** peak gyro ≥ 500 °/s (gyro from 100 ms before the start); decided ≤ 60 ms after the start; 300 ms refractory after an accepted swing. See "Swing threshold tuning". One MQTT message per swing to `ME193/CeciLaBarge/imu`, QoS 0: `{"swing": 1, "peak": <g>, "unit": "g", "peak_gyro": <°/s>, "t": <swing start, Unix s, board clock>}`; rejected candidates (`"swing": 0` + `"failed"`) go to `ME193/CeciLaBarge/imu/rejected`. `DEBUG = False` (True: per-sample CSV on the board + raw stream to `.../imu/raw`). Laptop: `python tools/imu_logger.py` prints swings and rejected moves.
- If `arduino-app-cli app logs` fails with `Error grabbing logs: invalid character`, Docker's log file for that app is corrupt (e.g. after a power loss): `arduino-app-cli app stop <app>; docker rm <app>-main-1; arduino-app-cli app start <app>` gives a fresh log.
- Labeled-data tools (not used so far): `tools/record_swings.py` (needs DEBUG = True on the board) and `tools/analyze_swings.py` (compares one signal at a time).
- **MQTT latency (for the Prompt 6 swing timing window):** broker.hivemq.com is AWS Frankfurt. Board→laptop delay of swing events measured with `imu_logger.py` on campus 2026-10-09: **110–485 ms** (test messages laptop→laptop ≈ 90–120 ms). Plus up to 60 ms from the detector's peak window. The game must match a swing that **arrives up to ~0.5 s after** the ball reaches the paddle line, so judge hits on arrival time with an asymmetric window (mostly after the contact moment), or use the event's `t` (board clock) if laptop and board clocks are synced; don't assume ~100 ms.
- Startup app: `arduino-app-cli properties set default /home/arduino/ArduinoApps/<app>` / `properties get default`. Currently **show_ip** (scrolls the IP; retries every 3 s showing "no IP" until one exists).
- **Plan (Phase 7):** once `pong_display` exists it becomes the startup app instead of show_ip, and it should scroll the IP whenever no game messages are arriving (reuse show_ip's IP code and the `host_ip.txt` cron file — the cron line points at the show_ip folder, so update it or keep that folder).
- `tools/advertise_board.py` (this repo) advertises the board over mDNS so App Lab can find it on Tufts WiFi.

## Status

- [x] CLAUDE.md written
- [x] Keyboard-only game: `laptop/game.py` (main loop) + `game_state.py` (rules, `is_hit`, state machine, no pygame) + `inputs.py` (paddle / swing / start sources) + `draw.py`. Run: `python laptop/game.py`. `GameState.update()` returns events (`hit`, `miss`, `record`, `serve`) for sounds/MQTT to hook into.
- [x] Camera paddle: `laptop/pose_input.py` (`PoseProcessor` in the shared camera thread + `CameraPaddle` with countdown calibration, EMA smoothing, freeze + "no hand"). Run: `python laptop/game.py --input camera` (keyboard stays default). Model: `assets/models/pose_landmarker_lite.task` (official Google file, same as in ME193-car). MediaPipe runs on the **unmirrored** frame (mirrored input swaps left/right labels); x and the preview are mirrored afterwards. Camera view is drawn inside the pygame window (no `cv2.imshow`). Built-in webcam: 640×480 works, ~23 pose updates/s.
- [x] AprilTags: `laptop/apriltag_input.py` (`TagProcessor` + `TagStart`). One camera thread for everything: `laptop/camera.py` runs a list of processors (pose, tags) per frame; pose_input/apriltag_input are processors, never open the camera themselves. Tags 0/1/2 → level, 5 → reset best (`GameState.reset_best`, event `best_reset`); a tag acts after `TAG_HOLD_S` held, once per showing; level tags only count down while the game can start (WAITING + calibrated). Keyboard (Space, 0-2) and tags both produce `("level"|"start"|"reset", value)` actions, applied in `game.apply_actions`. Flags: `--input camera` (pose + tags) or `--tags` (keyboard paddle + tags). Printable tags: `python tools/make_tags.py` → `assets/apriltags/` (Letter, 300 dpi, 6.4 in tags). Webcam with pose + tags: ~30 fps.
- [x] MQTT: `laptop/mqtt_client.py` (`GameMqtt`), broker `broker.hivemq.com:1883` (door-to-door project), paho VERSION2, `connect_async` + `loop_start` (auto-reconnect 1→10 s). Game state JSON `{"state","level","streak","x","y"}` (ball, 0..1; y=0 far wall) to GAME_TOPIC at 10 Hz, QoS 0, in every mode. On-screen "MQTT: connected/disconnected | record on broker N, publishing / not publishing (why)". `config.MQTT_ENABLED=False` plays offline. **Only the game thread calls `publish()`; paho callbacks only set flags** — paho holds `_out_message_mutex` while calling `on_publish`, so a lock shared with the game thread deadlocks the game. `GameMqtt` reads broker/topics from config when constructed (not as import-time defaults).
- [x] IMU in the game (Prompt 6): `mqtt_client` subscribes to `ME193/CeciLaBarge/imu` and keeps the latest swing (`last_swing` = `game_state.Swing(arrival=monotonic, peak=g)`, `last_swing_info` for the meter). **Hit rule** in `game_state.is_hit()` (comment block above it is the write-up text): ball reaches the paddle line ("contact") → paddle position judged AT CONTACT (out of zone = immediate miss) → a swing must ARRIVE in `[contact − SWING_WINDOW_BEFORE_S (0.15), contact + SWING_WINDOW_AFTER_S (0.55)]` (arrival time, because of the 110–485 ms delay). Returns True/False/None (None = keep waiting). While waiting the ball "presses into" the paddle (velocity decays with `HOLD_DECAY_S`, sinks ~15 px) with a closing ring; no swing by window close → ball drops through = miss. Each swing is used for one hit only. Return speed × `return_speed_factor(peak)`: 0.8 at ≤ 2.2 g → 1.6 at ≥ 5.5 g, linear, clamped; only the trip back (far wall restores level speed). Swing sources in `inputs.py`: camera → `ImuSwing`; keyboard → IMU **or Space** (`LatestSwing`); `--no-imu` (or MQTT off) → `AlwaysSwing`. Swing meter bottom right (flashes on each swing, shows peak g/dps, board→laptop delay, last hit's swing offset vs contact and return factor). Verified end-to-end through the real broker: swing sent at contact arrived +106 ms → hit; sent +400 ms arrived +496 ms → hit; none → miss at 557 ms.
- [ ] Next steps — wait for Cecilia to say which step to start

## Swing threshold tuning (2026-10-09, for the write-up)

**Round 1: acceleration only** (hand test with `tools/imu_logger.py`, detector at a 1.5 g placeholder). Real swings peaked at **1.5–5.2 g** (|a| − 1 g), most 2–4 g, about a quarter under 2.1 g. Fake moves (sliding, reaching, wiggling) peaked at **1.51, 1.62 and 2.11 g**. The ranges overlap, so no acceleration threshold separates them. Interim choice 2.0 g (rejected 2 of 3 fakes, cost: the weakest swings).

**Round 2: acceleration + rotation (gyro)** — raw output in `data/gyro_test.txt`. Every move above 2.0 g was reported with its peak accel and peak gyro (gyro peak counted from 100 ms before the start); #1–10 real swings, #11–15 fakes (sliding/reaching).

| # | label | peak accel (g) | peak gyro (°/s) | accel ≥ 2.2 | gyro ≥ 500 | result |
|---|---|---|---|---|---|---|
| 1 | swing | 3.94 | 712 | ✓ | ✓ | swing |
| 2 | swing | 5.66 | 934 | ✓ | ✓ | swing |
| 3 | swing | 2.85 | 817 | ✓ | ✓ | swing |
| 4 | swing | 5.79 | 921 | ✓ | ✓ | swing |
| 5 | swing | 2.56 | 686 | ✓ | ✓ | swing |
| 6 | swing | 4.38 | 540 | ✓ | ✓ | swing |
| 7 | swing | 2.75 | 726 | ✓ | ✓ | swing |
| 8 | swing | 4.53 | 906 | ✓ | ✓ | swing |
| 9 | swing | 4.59 | 781 | ✓ | ✓ | swing |
| 10 | swing | 3.34 | 614 | ✓ | ✓ | swing |
| 11 | fake | 2.37 | 477 | ✓ | ✗ | rejected |
| 12 | fake | 2.10 | 525 | ✗ | ✓ | rejected |
| 13 | fake | 2.14 | 577 | ✗ | ✓ | rejected |
| 14 | fake | 2.82 | 184 | ✓ | ✗ | rejected |
| 15 | fake | 2.18 | 357 | ✗ | ✗ | rejected |

(#16, 2.55 g / 350 °/s, 2 min later, was not labeled; the rule rejects it.)

- **Neither signal alone works:** swings 2.56–5.79 g vs fakes 2.10–2.82 g overlap in acceleration; swings 540–934 °/s vs fakes 184–577 °/s overlap in rotation.
- **Both together do:** a swing must have peak accel ≥ **2.2 g** AND peak gyro ≥ **500 °/s**. All 10 swings pass both checks; **each fake fails at least one**: the hard-but-not-turning moves (#11, #14) fail the gyro check, the turning-but-gentle ones (#12, #13) fail the accel check, #15 fails both. Physically: a real stroke is both a hard acceleration and a fast wrist rotation; sliding or reaching the paddle around is usually only one of the two.
- **Implementation** (`paddle_imu`, settings at the top of `main.py`): a candidate starts at 2.0 g (same as when the data was measured, so peaks are measured the same way), peaks are taken over ≤ 60 ms (gyro from 100 ms before), then both checks. 300 ms refractory only after an accepted swing. Rejected candidates go to `ME193/CeciLaBarge/imu/rejected` (tuning only; the game listens to `.../imu` alone).
- **Caveat — small sample, tight margins:** only 15 labeled moves, one session, one person. Several fakes are rejected by a single check and only just: #13 by 0.06 g, #12 by 0.10 g, #11 by 23 °/s; the closest real swings clear the thresholds by 0.36 g (#5) and 40 °/s (#6). **Needs a retest on the real paddle** (sensor mounted as in play, real returns, more fakes) before calling it final; expect the thresholds to move.
- **Why occasional false triggers are acceptable anyway:** a swing event alone never scores. The hit rule requires the paddle (wrist, from the camera) to be in the hit zone **and** a swing within the timing window around the moment the ball reaches the paddle. A fake trigger at any other time is ignored; it only matters inside that short window while the paddle is already in the right place, which is rare. Missed real swings are the costlier error.

## Record rules (ME193/Rogers/CeciLaBarge) — decided by Cecilia 2026-10-08

1. **Load first.** On connect, subscribe to the score topic and read the retained value = all-time record; `best_streak` starts from it (or stays higher if this session already beat it). **Nothing is published on the score topic until the record has loaded** (no retained value within `RECORD_LOAD_WAIT_S` = 2 s after SUBACK → topic counts as empty).
2. **Topic always matches the game's record.** After loading, whenever `best_streak` ≠ the broker's value, publish `str(float(best_streak))`, QoS 1, retained: beating the record (up) **and tag-5 resets (down)**. A tag-5 reset before the record loaded wins over the loaded value. Re-sent after a reconnect if never confirmed. Payload is only the float; nothing else ever goes on this topic.
3. **Only real play publishes.** `publish_score = --input camera AND IMU active (MQTT on, no --no-imu) AND not --no-publish` (updated 2026-10-09, Prompt 6). Keyboard, `--tags` and `--no-imu` modes connect, load and show the record but never publish to the score topic. `--no-publish` blocks score publishing in any mode (game topic still sent). In camera mode only real IMU swings count (no Space swing), so a published record always comes from real paddle swings.
4. Tools: `python tools/watch_score.py` (print every update, flags non-floats), `python tools/clear_score.py` (shows the retained value, asks to type "clear", then publishes an empty retained message). Both take `--topic` for test topics.
5. **Testing rule: never let tests reach the real score topic.** Test harnesses must set `config.MQTT_ENABLED = False` or point `SCORE_TOPIC` at `ME193/CeciLaBarge/test-<random>/...` (and clear retained test values afterwards). A test run on 2026-10-08 accidentally left a retained `1.0` on the real topic (old code published from keyboard/--tags mode).
