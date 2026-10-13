# ME193-pingpong

ME193 robotics midterm: a virtual ping-pong game. A ball moves on the laptop
screen (pygame), and you hit it back with a real paddle.

- **Paddle position:** MediaPipe pose tracking of your wrist on the webcam
- **Swing:** accelerometer on the paddle → Arduino UNO Q → MQTT
- **Hit:** paddle in the hit zone **and** a swing within a short timing window
- **Start / level:** hold up an AprilTag; its ID picks the level
- **Goal:** as many volleys in a row as possible; the opponent never misses and every hit makes the next ball faster and wider
- **Record:** best volley count, published to MQTT
- **LED matrix:** the UNO Q shows the ball as a dot
- **Pick your opponent:** hold up a blue or green minifig (YOLOv8 from the mini-fig-yolo project); a blocky blue or green figure plays at the far end, moving with motions you can record yourself
- **Voice commands (Vosk, offline):** "smash", "play again", "level one/two/three", "pause", "resume", "score", "blue", "green"
- **Keyboard fallback:** playable with no camera or board

## Layout

```
laptop/   the game (Python, runs on the laptop); every setting is in laptop/config.py
tools/    helpers: AprilTag printing, motion capture, voice / MQTT / IMU test tools
assets/   sounds, AprilTag pages, models (pose, minifig YOLO), recorded motions
docs/     write-up material and screenshots of every screen
data/     session logs written by the game (git-ignored) + session_results.md
unoq/     empty: the board code lives in github.com/clabar01/ArduinoApps (paddle_imu, local_broker)
```

## Install (macOS, Python 3.14)

```bash
git clone https://github.com/clabar01/ME193-pingpong.git && cd ME193-pingpong
brew install portaudio          # needed by pyaudio (no Homebrew? see CLAUDE.md, "pyaudio needs PortAudio")
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Then download the voice model (next section). The pose and minifig models are
already in `assets/models/`. Print the AprilTags with `python tools/make_tags.py`
(pages in `assets/apriltags/`: tags 0/1/2 = Beginner/Club/Pro, 5 = reset the
record, 10 = SLOW-MO card, 11 = EXTRA LIFE card).

**Board (optional):** the paddle's swing sensor runs on the Arduino UNO Q
(`paddle_imu` in the ArduinoApps repo). The board's address is set in ONE place,
`~/ArduinoApps/tools/board_secrets.py` (git-ignored; copy of
`board_secrets.example.py`): set `BOARD_IP` when you change WiFi. The game also
tries every address in `HOSTS` and tells you which one answered. No passwords or
API keys are needed: the MQTT brokers (the board's own Mosquitto and
broker.hivemq.com) are open.

## Run

```bash
source venv/bin/activate
python laptop/game.py --input camera            # the real game: webcam paddle + AprilTags + paddle IMU
python laptop/game.py                           # keyboard only (no camera, no board needed)
python laptop/game.py --input camera --no-imu   # camera, but every ball counts as swung (no board)
```

| Flag | What it does |
|---|---|
| `--input camera` | wrist tracking with the webcam + AprilTags + minifig opponent pick (default: `keyboard`) |
| `--tags` | AprilTags with the keyboard paddle |
| `--no-imu` | ignore the paddle sensor: every ball counts as swung |
| `--no-publish` | never publish the record to `ME193/Rogers/CeciLaBarge` (use for testing) |
| `--opponent blue` / `green` | skip the "Pick your opponent" screen |
| `--no-voice` | no microphone, no speech |
| `--debug` | start with the debug overlay on |

The record is only published with `--input camera` and real IMU swings.

**Order of screens:** pick your opponent → calibrate (reach left, right, up,
down when asked) → pick a level (AprilTag 0/1/2 or keys 1/2/3) → 3-2-1-GO →
play → GAME OVER (swing, Space or a level card to play again).

| Key | What it does |
|---|---|
| B / G | pick the blue / green opponent |
| 1 / 2 / 3 | start at Beginner / Club / Pro; Space = start / play again at the current level |
| arrow keys + Space | keyboard paddle: move, swing |
| S / L | SLOW-MO / EXTRA LIFE card (stand-ins for tags 10 / 11) |
| X / P / T | voice stand-ins: smash, pause/resume, score |
| C | recalibrate the camera (start screen) |
| D | debug overlay on / off |
| Esc | quit (prints a session summary; logs in `data/`) |

### Voice commands (Vosk)

The speech model is not in git (about 70 MB). Download it once into `models/`:

```bash
mkdir -p models && cd models
curl -LO https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip && rm vosk-model-small-en-us-0.15.zip
cd ..
```

The first run asks for microphone access (allow it for Terminal / VS Code in
System Settings > Privacy & Security > Microphone). Check it with
`python tools/voice_test.py --live` (prints what it hears), or measure accuracy
with `python tools/voice_test.py --live --prompt 20`.

| Say | What it does |
|---|---|
| "smash" | any time in a rally: arms a SMASH for the next ball that reaches you; hit it and it goes back 1.5x faster (once per rally) |
| "play again" | on the GAME OVER screen: play again (same level) |
| "level one" / "level two" / "level three" | on the start or GAME OVER screen: play Beginner / Club / Pro |
| "pause" / "resume" | pause and unpause (during a rally, the 3-2-1 or the extra-life countdown; if a ball is at your paddle, right after it) |
| "score" | says your volley count and best out loud |
| "blue" / "green" | on the opponent select screen: pick that opponent (also switches it on the start / GAME OVER screen) |

Next to the mic icon the game says what became of each command: white = done ("smash: armed"),
grey = soon ("pause: after this ball"), red = not done and why ("smash: used",
"play again: wait", "level two: not sure"). Keyboard stand-ins: X = smash, P = pause/resume, T = score. `python laptop/game.py --no-voice`
turns voice off (no microphone, no speech). No microphone or no model: the
game prints one message and plays on.

### Opponent

First screen: **Pick your opponent**. Hold a blue or green minifig up to the
camera for about a second, or press **B** / **G**, or say "blue" / "green".
The detector is the YOLOv8n trained in mini-fig-yolo (`models/best.pt`),
converted once to `assets/models/minifig_yolov8n.onnx` and run with OpenCV
(`tools/export_minifig_onnx.py` documents the conversion; no ultralytics or
torch needed here). It only runs on that screen. `--opponent blue` skips it.

The figure's moves come from `assets/motions/*.json`, recorded from your own
body with MediaPipe (missing ones are drawn procedurally):

```bash
python tools/record_swing.py          # idle, forehand, backhand, side step (guided; SPACE starts each)
python tools/record_swing.py --view   # watch the result on both figures (keys 1-4)
python tools/record_swing.py --clear  # back to the built-in motions
```
