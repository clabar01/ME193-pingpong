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
- pyaudio needs `brew install portaudio` before `pip install pyaudio`.

## Status

- [x] CLAUDE.md written (no game code yet)
- [ ] Everything else — wait for Cecilia to say which step to start
