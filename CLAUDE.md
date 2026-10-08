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

Broker address, topics, and credentials are configured in `config.py` / `board_secrets.py` — never hard-coded elsewhere.

## Rules for working on this project

- **Python on the laptop.** (UNO Q side uses whatever the board needs — Arduino sketch and/or its Python app.)
- **All tunable values live in one `config.py`:** thresholds, timing window, ball speeds, hit-zone sizes per level, AprilTag ID → level map, MQTT broker/port/topics, camera index, screen size, etc. No magic numbers scattered in other files.
- **Threads:** camera (MediaPipe + AprilTag), MQTT, and audio each run in **their own thread** so the pygame loop never blocks. Threads share state through thread-safe structures (locks / queues / latest-value holders); the game loop only reads the latest values.
- **Keyboard fallback always works:** a keyboard paddle mode (arrow keys move the paddle, a key such as Space acts as a "swing") must remain available so the game is playable/demoable with no camera or no UNO Q.
- **Never commit secrets:** `board_secrets.py` and any API keys (e.g. LLM / TTS keys) must be in `.gitignore`. Provide a `board_secrets_example.py` template with placeholder values instead.
- **Explain the logic in comments:** the hit rule and the swing-detection logic must have clear, plain-language comments (what is checked, why, which config values control it) — Cecilia quotes these in her write-up.
- **After every step, give exact testing instructions:** commands to run, what to do physically (e.g. "hold tag 3 up to the camera"), and what she should see/hear if it works.
- Build in small steps; don't write code beyond the current step.

## Reuse from past projects

Past repos (only read them once Cecilia grants access):
- MediaPipe pose tracking — "Pose race": github.com/clabar01/ME193-car
- AprilTag detection — "AprilTag parking": github.com/clabar01/ME193-car
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
- Switching home ↔ campus: change **`BOARD_IP`** there; nothing else. `HOSTS` already lists both: `["10.247.137.172" (Tufts_Robotics, unconfirmed), "192.168.1.185" (home), "AirFour.local"]`.

## UNO Q notes

- Board: AirFour, user `arduino`, home IP 192.168.1.185, Tufts IP likely 10.247.137.172.
- App Python runs in a Docker container on its own network: `wlan0` is not visible and socket tricks return a 172.x address. App Lab sets `HOST_IP` at app start (fixed afterwards). For a live value, the board's crontab runs `show_ip/write_host_ip.sh` every minute, writing the wlan0 IP to `~/ArduinoApps/show_ip/host_ip.txt` (`/app/host_ip.txt` inside the show_ip container; empty = no WiFi).
- LED matrix pattern (from `minifig_tracker`): Python owns the logic and calls Bridge providers in the sketch (`show_dot(col,row)`, `clear_matrix()`); the sketch guards matrix writes with a `K_MUTEX`. Matrix is 13 cols × 8 rows. For text, include `ArduinoGraphics.h` before `Arduino_LED_Matrix.h` and add `ArduinoGraphics (1.1.4)` to `sketch.yaml` (see `show_ip`).
- Startup app: `arduino-app-cli properties set default /home/arduino/ArduinoApps/<app>` / `properties get default`. Currently **show_ip** (scrolls the IP; retries every 3 s showing "no IP" until one exists).
- **Plan (Phase 7):** once `pong_display` exists it becomes the startup app instead of show_ip, and it should scroll the IP whenever no game messages are arriving (reuse show_ip's IP code and the `host_ip.txt` cron file — the cron line points at the show_ip folder, so update it or keep that folder).
- `tools/advertise_board.py` (this repo) advertises the board over mDNS so App Lab can find it on Tufts WiFi.

## Status

- [x] CLAUDE.md written (no game code yet)
- [ ] Everything else — wait for Cecilia to say which step to start
