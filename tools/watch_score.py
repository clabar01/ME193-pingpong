"""Watch the record topic: prints every message published on SCORE_TOPIC.

Run from the repo root:  python tools/watch_score.py      (Ctrl+C to stop)

On connect the broker first sends the retained value (marked "retained"),
then every new record as the game publishes it. Each payload is checked: the
topic must only ever carry a float like "12.0", so anything else is flagged.
Broker and topic come from laptop/config.py.
"""
import argparse
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "laptop"))
import config  # noqa: E402


def check(payload: bytes) -> str:
    """'ok' if the payload is a plain float like "12.0", else what's wrong."""
    text = payload.decode("utf-8", errors="replace")
    if text == "":
        return "cleared (empty message deletes the retained value)"
    try:
        value = float(text)
    except ValueError:
        return f"NOT A FLOAT: {text!r}"
    if "." not in text:
        return f"number but not float-formatted: {text!r} (expected e.g. {value:.1f})"
    return "ok"


def main():
    parser = argparse.ArgumentParser(description="Print every update on the record topic")
    parser.add_argument("--topic", default=config.SCORE_TOPIC,
                        help=f"topic to watch (default: {config.SCORE_TOPIC})")
    args = parser.parse_args()
    stopping = False

    def on_connect(client, userdata, flags, reason_code, properties):
        print(f"Connected to {config.MQTT_BROKER}:{config.MQTT_PORT} ({reason_code}); "
              f"watching {args.topic}", flush=True)
        client.subscribe(args.topic, qos=1)

    def on_disconnect(client, userdata, flags, reason_code, properties):
        if not stopping:
            print(f"Disconnected ({reason_code}); reconnecting...", flush=True)

    def on_message(client, userdata, msg):
        stamp = time.strftime("%H:%M:%S")
        kind = "retained" if msg.retain else "live    "
        text = msg.payload.decode("utf-8", errors="replace")
        print(f"{stamp}  {kind}  {msg.topic}  {text!r:>10}  [{check(msg.payload)}]", flush=True)

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
        stopping = True
        client.disconnect()


if __name__ == "__main__":
    main()
