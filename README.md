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
- **Voice commands (Vosk, offline):** "smash", "play again", "level one/two/three", "pause", "resume", "score"
- **Keyboard fallback:** playable with no camera or board

## Layout

```
laptop/   Python game, runs on the laptop (all settings in laptop/config.py)
unoq/     Code for the Arduino UNO Q (accelerometer + LED matrix)
assets/   sounds/ and apriltags/
docs/     write-up material
```

## Setup (macOS)

```bash
brew install portaudio          # needed by pyaudio (without Homebrew: see CLAUDE.md)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Broker credentials go in `laptop/board_secrets.py`, which is git-ignored.

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
| "smash" | while the ball is coming to you: your return goes back 1.5x faster (once per rally) |
| "play again" | on the GAME OVER screen: play again (same level) |
| "level one" / "level two" / "level three" | on the start or GAME OVER screen: play Beginner / Club / Pro |
| "pause" / "resume" | pause and unpause a rally |
| "score" | says your volley count and best out loud |

Keyboard stand-ins: X = smash, P = pause/resume, T = score. `python laptop/game.py --no-voice`
turns voice off (no microphone, no speech). No microphone or no model: the
game prints one message and plays on.
