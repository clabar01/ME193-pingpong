"""Watch swing events from the paddle (UNO Q paddle_imu app).

Run from the repo root:  python tools/imu_logger.py      (Ctrl+C to stop)

Subscribes to IMU_TOPIC and prints every swing with its peak (accel, and the
peak rotation speed in deg/s) and the time since the previous swing. Also
prints moves the board REJECTED (IMU_REJECTED_TOPIC: started like a swing but
failed the accel or gyro check), marked "rejected", with the check(s) failed.
Swing numbers count only real swings. Also shows how long the message took from the board
to this laptop (only meaningful if both clocks are synced, which they normally
are via the internet). Broker and topic come from laptop/config.py.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "laptop"))
import config  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Print swing events from the paddle IMU")
    parser.add_argument("--topic", default=config.IMU_TOPIC,
                        help=f"topic to watch (default: {config.IMU_TOPIC})")
    args = parser.parse_args()

    state = {"count": 0, "prev_t": None, "stopping": False}

    rejected_topic = args.topic + "/rejected"

    def on_connect(client, userdata, flags, reason_code, properties):
        print(f"Connected to {config.MQTT_BROKER}:{config.MQTT_PORT} ({reason_code}); "
              f"watching {args.topic} (+ /rejected). Swing the paddle!", flush=True)
        client.subscribe(args.topic)
        client.subscribe(rejected_topic)

    def on_disconnect(client, userdata, flags, reason_code, properties):
        if not state["stopping"]:
            print(f"Disconnected ({reason_code}); reconnecting...", flush=True)

    def on_message(client, userdata, msg):
        arrived = time.time()
        stamp = time.strftime("%H:%M:%S", time.localtime(arrived)) + f".{int(arrived * 1000) % 1000:03d}"
        try:
            event = json.loads(msg.payload)
            t, peak = float(event["t"]), float(event["peak"])
            unit = event.get("unit", "g")
            gyro = event.get("peak_gyro")
            is_swing = msg.topic == args.topic
            assert event.get("swing") == (1 if is_swing else 0)
        except (ValueError, KeyError, TypeError, AssertionError):
            print(f"{stamp}  unexpected message: {msg.payload[:200]!r}", flush=True)
            return
        gyro_text = f"gyro {gyro:5.0f} dps" if gyro is not None else "gyro    -    "
        if not is_swing:
            failed = ", ".join(event.get("failed", []))
            print(f"{stamp}    rejected  peak {peak:5.2f} {unit:3s} {gyro_text}  ({failed})", flush=True)
            return
        state["count"] += 1
        gap = "first swing" if state["prev_t"] is None else f"+{t - state['prev_t']:6.3f} s since previous"
        state["prev_t"] = t
        delay_ms = (arrived - t) * 1000
        delay = f"{delay_ms:5.0f} ms board->laptop" if -2000 < delay_ms < 10000 else "clocks not in sync"
        print(f"{stamp}  SWING #{state['count']:<3d} peak {peak:5.2f} {unit:3s} {gyro_text}  "
              f"{gap:24s}  ({delay})",
              flush=True)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(config.MQTT_RECONNECT_MIN_S, config.MQTT_RECONNECT_MAX_S)
    client.connect_async(config.MQTT_BROKER, config.MQTT_PORT, keepalive=config.MQTT_KEEPALIVE_S)
    try:
        client.loop_forever(retry_first_connection=True)
    except KeyboardInterrupt:
        pass
    finally:
        state["stopping"] = True
        client.disconnect()


if __name__ == "__main__":
    main()
