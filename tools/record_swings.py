"""Record labeled IMU data: press s (swing) or f (fake move), then do it.

Run from the repo root, with paddle_imu running on the board in DEBUG mode
(it streams raw samples to IMU_RAW_TOPIC):

    python tools/record_swings.py

Keys (no Enter needed):
    s   label the next ~1 s as a real SWING (press, then swing)
    f   label the next ~1 s as a FAKE move (press, then do a non-swing move:
        wave, shake, tap the paddle, set it down, turn it over...)
    u   undo the last trial (pressed the wrong key / messed it up)
    q   quit (data is saved after every trial anyway)

Each trial keeps the samples from about WINDOW_BEFORE_S before the key press
to WINDOW_AFTER_S after it, timed on the board's clock so a laptop/board clock
difference doesn't matter. All trials of a session go to
data/imu_labeled_<date-time>.csv. Analyze with:  python tools/analyze_swings.py
"""
import csv
import json
import math
import select
import sys
import termios
import threading
import time
import tty
from pathlib import Path

import paho.mqtt.client as mqtt

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "laptop"))
import config  # noqa: E402

DATA_DIR = REPO_ROOT / "data"
WINDOW_BEFORE_S = 0.2   # keep a little before the press (you may start moving early)
WINDOW_AFTER_S = 1.1    # ~1 s to do the move
LABELS = {"s": "swing", "f": "fake"}
COLUMNS = ["trial", "label", "t", "ax", "ay", "az", "gx", "gy", "gz", "accel_mag", "gyro_mag"]

lock = threading.Lock()
samples = []            # [t, ax, ay, az, gx, gy, gz] as they arrive (board clock)
last_arrival = [0.0]


def on_connect(client, userdata, flags, reason_code, properties):
    client.subscribe(config.IMU_RAW_TOPIC)


def on_message(client, userdata, msg):
    try:
        batch = json.loads(msg.payload)["s"]
    except (ValueError, KeyError, TypeError):
        return
    with lock:
        samples.extend(batch)
        del samples[:-3000]   # keep the last ~30 s
        last_arrival[0] = time.time()


def trial_rows(trial: int, label: str, window):
    rows = []
    for t, ax, ay, az, gx, gy, gz in window:
        accel = math.sqrt(ax * ax + ay * ay + az * az) - 1.0
        gyro = math.sqrt(gx * gx + gy * gy + gz * gz)
        rows.append([trial, label, f"{t:.3f}", ax, ay, az, gx, gy, gz, f"{accel:.3f}", f"{gyro:.1f}"])
    return rows


def save(path: Path, trials):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for rows in trials:
            w.writerows(rows)


def say(text=""):
    # The terminal is in cbreak mode: "\r\n" keeps lines starting at the left edge.
    sys.stdout.write(text + "\r\n")
    sys.stdout.flush()


def record(label: str, trial: int):
    """Grab the window around this key press, once all its samples have arrived."""
    with lock:
        if not samples or time.time() - last_arrival[0] > 1.0:
            say("  !! no raw data arriving: is paddle_imu running with DEBUG = True?")
            return None
        anchor = samples[-1][0]   # newest board time we have at the press
    start, end = anchor - WINDOW_BEFORE_S, anchor + WINDOW_AFTER_S
    say(f"  [{label.upper()}] go! recording ~{WINDOW_AFTER_S:.1f} s ...")
    deadline = time.time() + WINDOW_AFTER_S + 3.0
    while time.time() < deadline:
        with lock:
            if samples[-1][0] >= end:
                window = [s for s in samples if start <= s[0] <= end]
                break
        time.sleep(0.02)
    else:
        say("  !! data stopped arriving; trial discarded")
        return None
    rows = trial_rows(trial, label, window)
    peak_a = max(float(r[9]) for r in rows)
    peak_g = max(float(r[10]) for r in rows)
    say(f"  trial {trial:3d} {label:5s}: peak accel {peak_a:5.2f} g   peak gyro {peak_g:7.1f} dps"
        f"   ({len(rows)} samples)")
    return rows


def main():
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"imu_labeled_{time.strftime('%Y%m%d-%H%M%S')}.csv"

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect_async(config.MQTT_BROKER, config.MQTT_PORT, keepalive=config.MQTT_KEEPALIVE_S)
    client.loop_start()

    print(f"Listening on {config.IMU_RAW_TOPIC} ... saving to {path.relative_to(REPO_ROOT)}")
    print("Keys: s = swing, f = fake move, u = undo last, q = quit")
    trials = []
    old = termios.tcgetattr(sys.stdin)
    tty.setcbreak(sys.stdin.fileno())   # read single key presses, no Enter
    try:
        waiting_said = False
        while True:
            with lock:
                live = samples and time.time() - last_arrival[0] < 1.0
            if not live and not waiting_said:
                say("(waiting for raw data from the board...)")
                waiting_said = True
            elif live and waiting_said:
                say("raw data arriving - ready.")
                waiting_said = False
            if not select.select([sys.stdin], [], [], 0.2)[0]:
                continue
            key = sys.stdin.read(1).lower()
            if key == "q":
                break
            if key == "u":
                if trials:
                    gone = trials.pop()
                    save(path, trials)
                    say(f"  undid trial {gone[0][0]} ({gone[0][1]})")
                continue
            if key in LABELS:
                rows = record(LABELS[key], len(trials) + 1)
                if rows:
                    trials.append(rows)
                    save(path, trials)
                    n_s = sum(r[0][1] == "swing" for r in trials)
                    say(f"  saved. {n_s} swings, {len(trials) - n_s} fakes so far")
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
        client.loop_stop()
        client.disconnect()
    if trials:
        print(f"\nSaved {len(trials)} trials to {path.relative_to(REPO_ROOT)}")
        print("Next: python tools/analyze_swings.py")
    else:
        print("\nNo trials recorded.")


if __name__ == "__main__":
    main()
