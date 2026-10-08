# ME193-pingpong

ME193 robotics midterm: a virtual ping-pong game. A ball moves on the laptop
screen (pygame), and you hit it back with a real paddle.

- **Paddle position:** MediaPipe pose tracking of your wrist on the webcam
- **Swing:** accelerometer on the paddle → Arduino UNO Q → MQTT
- **Hit:** paddle in the hit zone **and** a swing within a short timing window
- **Start / level:** hold up an AprilTag; its ID picks the level
- **Record:** best streak of continuous hits, published to MQTT
- **LED matrix:** the UNO Q shows the ball as a dot
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
brew install portaudio          # needed by pyaudio
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Broker credentials go in `laptop/board_secrets.py`, which is git-ignored.
