"""Clear the retained record on the score topic (e.g. after a bad test value).

Run from the repo root:  python tools/clear_score.py

Shows the value currently retained on SCORE_TOPIC, then asks you to type
"clear" to confirm. Clearing = publishing an empty retained message, which is
how MQTT deletes a retained value. (Anyone subscribed right then sees one
empty message; new subscribers then get nothing until the game publishes a
record again.)

--topic lets you clear a test topic instead. Broker and topic come from
laptop/config.py.
"""
import argparse
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "laptop"))
import config  # noqa: E402

READ_WAIT_S = 3   # how long to wait for the retained value


def main():
    parser = argparse.ArgumentParser(description="Clear the retained record on the score topic")
    parser.add_argument("--topic", default=config.SCORE_TOPIC,
                        help=f"topic to clear (default: {config.SCORE_TOPIC})")
    args = parser.parse_args()

    retained = []
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, u, f, rc, p: c.subscribe(args.topic, qos=1)
    client.on_message = lambda c, u, m: retained.append(m.payload) if m.retain else None
    try:
        client.connect(config.MQTT_BROKER, config.MQTT_PORT, keepalive=config.MQTT_KEEPALIVE_S)
    except OSError as e:
        sys.exit(f"Can't reach {config.MQTT_BROKER}:{config.MQTT_PORT}: {e}")
    client.loop_start()
    time.sleep(READ_WAIT_S)

    print(f"Broker: {config.MQTT_BROKER}:{config.MQTT_PORT}")
    print(f"Topic:  {args.topic}")
    if not retained:
        print("Nothing is retained on this topic. Nothing to clear.")
        client.loop_stop()
        client.disconnect()
        return
    print(f"Retained value now: {retained[-1].decode(errors='replace')!r}")

    try:
        answer = input('Type "clear" to delete it, anything else to cancel: ').strip()
    except EOFError:
        answer = ""
    if answer != "clear":
        print("Cancelled. Nothing changed.")
    else:
        client.unsubscribe(args.topic)   # don't count our own empty message
        client.publish(args.topic, b"", qos=1, retain=True).wait_for_publish(10)
        print("Cleared. New subscribers will get no retained value.")
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
