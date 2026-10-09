"""MQTT: publishes the record and the game state. Never blocks or crashes the game.

Broker settings are the door-to-door minifig project's (broker.hivemq.com:1883,
paho-mqtt with CallbackAPIVersion.VERSION2), set in config.py.

Two kinds of messages:

  SCORE_TOPIC  (ME193/Rogers/CeciLaBarge)
      ONLY the record number of continuous hits, as a float, e.g. "12.0",
      retained, sent each time best_streak goes up. Nothing else is ever
      published on this topic.
  GAME_TOPIC   (ME193/CeciLaBarge/game)
      About GAME_PUBLISH_HZ times a second, JSON for the UNO Q LED matrix:
      {"state": "PLAYING", "level": 1, "streak": 3, "x": 0.512, "y": 0.233}
      x, y = ball center, 0..1 (x: 0 = left wall; y: 0 = far wall, 1 = bottom
      of the screen, the player's side). Not retained.

Networking runs in paho's own background thread (loop_start), which also
reconnects automatically if the connection drops. If the broker is down the
game keeps running; publishes are simply skipped, except the record, which is
re-sent after reconnecting if it never reached the broker.
"""
import json
import time

import paho.mqtt.client as mqtt

import config
import game_state as gs


class GameMqtt:
    def __init__(self, broker: str = config.MQTT_BROKER, port: int = config.MQTT_PORT,
                 score_topic: str = config.SCORE_TOPIC, game_topic: str = config.GAME_TOPIC):
        self.broker, self.port = broker, port
        self.score_topic, self.game_topic = score_topic, game_topic
        self.connected = False      # shown on screen; written by paho's thread
        self._stopping = False
        self._connections = 0       # bumped on every (re)connect, by paho's thread
        self._acked = set()         # message ids the broker confirmed (QoS 1)
        # Record bookkeeping, touched ONLY by the game thread (see update()):
        self._record = None         # newest record to publish (float), or None
        self._sent = None           # (value, mid, connection number) of the last send
        self._last_best = 0
        self._last_game_publish = 0.0

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_connect_fail = self._on_connect_fail
        self.client.on_publish = self._on_publish
        self.client.reconnect_delay_set(config.MQTT_RECONNECT_MIN_S, config.MQTT_RECONNECT_MAX_S)
        # connect_async + loop_start: connecting (and every retry) happens in
        # paho's thread, so the game starts even with no network.
        self.client.connect_async(self.broker, self.port, keepalive=config.MQTT_KEEPALIVE_S)
        self.client.loop_start()

    # ------------------------------------------------------------ game -> MQTT
    def update(self, game: gs.GameState):
        """Call once per frame from the game loop. Publishes the record when it
        goes up, and the game state at GAME_PUBLISH_HZ. Never blocks."""
        if game.best_streak > self._last_best:
            self._record = float(game.best_streak)
        # Follow resets down too (tag 5), so the next increase is detected,
        # but don't publish them: only increases go to SCORE_TOPIC.
        self._last_best = game.best_streak
        self._send_record_if_needed()

        now = time.monotonic()
        if now - self._last_game_publish >= 1 / config.GAME_PUBLISH_HZ:
            self._last_game_publish = now
            self.publish_game(game)

    def publish_game(self, game: gs.GameState):
        if not self.connected:
            return   # nothing to do offline; the next message replaces this one anyway
        msg = {
            "state": game.state,
            "level": game.level,
            "streak": game.streak,
            "x": round(game.ball.x / game.width, 3),
            "y": round(game.ball.y / game.height, 3),
        }
        self.client.publish(self.game_topic, json.dumps(msg), qos=0, retain=False)

    def stop(self):
        self._stopping = True
        self.client.disconnect()
        self.client.loop_stop()

    # ------------------------------------------------------------ internals
    def _send_record_if_needed(self):
        """Send the newest record (QoS 1, retained) unless the broker already
        has it or it's on its way over the current connection.

        Only the game thread sends records, so they always go out in order and
        an older value can never overwrite a newer one. paho's thread only
        flips flags (connected, _connections, _acked); it never takes a lock
        the game thread holds, so the two can't deadlock.
        """
        if self._record is None or not self.connected:
            return   # offline: tried again every frame until the connection is back
        if self._sent is not None and self._sent[0] == self._record:
            value, mid, connection = self._sent
            if mid in self._acked:
                return   # broker has it
            if connection == self._connections:
                return   # still in flight on this connection
            # else: the connection dropped before the broker confirmed it: resend
        payload = str(self._record)   # e.g. "12.0": just the float, nothing else
        info = self.client.publish(self.score_topic, payload, qos=1, retain=True)
        self._sent = (self._record, info.mid, self._connections)
        print(f"MQTT: record {payload} -> {self.score_topic} (retained)")

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if not reason_code.is_failure:
            self._connections += 1
        self.connected = not reason_code.is_failure
        print(f"MQTT: connect to {self.broker}:{self.port}: {reason_code}")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if self.connected and not self._stopping:
            print(f"MQTT: disconnected ({reason_code}); reconnecting automatically")
        self.connected = False

    def _on_connect_fail(self, client, userdata):
        self.connected = False

    def _on_publish(self, client, userdata, mid, reason_code, properties):
        self._acked.add(mid)   # set.add is atomic; no lock needed
