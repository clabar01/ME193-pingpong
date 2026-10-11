# Play-test results, 2026-10-11

Session summaries from the game's end-of-session printout (per-ball logs:
`data/balls_<time>.csv`, kept locally, not in git). Network: 29BCNet, board
AirFour at 192.168.4.78.

## Before the fixes

| Session | Balls | Hits | Hit rate |
|---|---|---|---|
| Real paddle, before the fixes | 48 | 0 | 0% |

## After the fixes

| Session | Level | Hits / balls | Hit rate | Best streak |
|---|---|---|---|---|
| `--tags` (arrow keys + real IMU swings) | | 6 / 14 | 43% | |
| `--input camera --no-imu` (camera paddle, every ball swung) | Beginner | 42 / 48 | 88% | 25 |
| Full game (`--input camera`, real IMU swings) | Club | 25 / 44 | 57% | 9 |

## Swing delay (swing on the paddle -> event on the laptop)

| Path | Delay |
|---|---|
| Local MQTT broker on the UNO Q | median 42 ms |
| broker.hivemq.com (internet) | 600 to 1500 ms |

## What fixed it

- **Local MQTT broker** on the UNO Q (Mosquitto in Docker, bridged to
  hivemq): swings reach the laptop in tens of ms instead of 0.6 to 1.5 s, so
  they land in the timing window.
- **Target marker:** a box on the hitting plane shows where the ball will
  cross it (faint as soon as the opponent hits, solid after the bounce),
  sized to the level's hit zone and green when the paddle is in position.
- **Height calibration:** the camera calibration has four steps (left,
  right, high, low) instead of a fixed wrist-height mapping.
- **Robust hand tracking:** when the paddle hides the wrist, the hand
  position comes from the knuckles or the elbow plus the last forearm offset,
  and the paddle holds its position for 0.3 s instead of freezing.
- **Recentered swing window:** ±0.35 s around contact when swings come
  through the local broker (it was -0.15 / +0.6 s, shifted late for the
  internet delay; that window is still used when falling back to hivemq).
