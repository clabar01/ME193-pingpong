"""MQTT: the all-time record and the game state. Never blocks or crashes the game.

Broker settings are the door-to-door minifig project's (broker.hivemq.com:1883,
paho-mqtt with CallbackAPIVersion.VERSION2), set in config.py.

Two topics:

  SCORE_TOPIC  (ME193/Rogers/CeciLaBarge)
      ONLY the record number of continuous hits, as a float, e.g. "12.0",
      retained. Nothing else is ever published on this topic.
  GAME_TOPIC   (ME193/CeciLaBarge/game)
      About GAME_PUBLISH_HZ times a second, JSON for the UNO Q LED matrix:
      {"state": "PLAYING", "level": 1, "streak": 3, "x": 0.512, "y": 0.233, "h": 0.31}
      x, y = ball position seen from above the table, 0..1 (x: 0 = left edge,
      1 = right; y: 0 = the opponent's end, 1 = my end); h = height above the
      table in meters. Not retained. Sent in every mode.

THE RECORD RULES
  1. Load first. On connecting, the game subscribes to SCORE_TOPIC and reads
     the retained value: the all-time record. best_streak starts from it
     (or stays higher if this session already beat it). Until the record is
     loaded, nothing is published on SCORE_TOPIC, so a game started offline
     can never overwrite a higher record. If no retained value arrives within
     RECORD_LOAD_WAIT_S of subscribing, the topic is treated as empty.
  2. Keep the topic equal to the game's record. After loading, whenever
     best_streak differs from what the broker holds, publish float(best_streak),
     retained. That covers beating the record (goes up) and a tag-5 reset
     (goes down). A tag-5 reset made before the record loaded wins over the
     loaded value.
  3. Only real play publishes. publish_score is True only with
     --input camera AND the IMU active (no --no-imu), and False with the
     keyboard, --tags, --no-imu, or --no-publish. With it False the game still connects, loads,
     and shows the record, but never publishes on SCORE_TOPIC.

Networking runs in paho's own background thread (loop_start), which also
reconnects automatically. Only the game thread calls publish(); paho's
callbacks just set flags (paho holds an internal lock while calling
on_publish, so sharing a lock with the game thread could deadlock the game).
"""
import json
import time

import paho.mqtt.client as mqtt

import config
import game_state as gs
from game_state import Swing


class GameMqtt:
    def __init__(self, publish_score: bool, why_not: str = "",
                 broker=None, port=None, score_topic=None, game_topic=None):
        """publish_score: may this session publish the record (see rule 3)?
        why_not: short reason shown on screen when it may not, e.g. "keyboard".
        broker/port/topics default to config.py, read now (not at import time),
        so a test that points config at a test topic really uses it."""
        self.publish_score, self.why_not = publish_score, why_not
        self.broker = broker or config.MQTT_BROKER
        self.port = port or config.MQTT_PORT
        self.score_topic = score_topic or config.SCORE_TOPIC
        self.game_topic = game_topic or config.GAME_TOPIC

        # Written by paho's thread, read by the game thread (plain flags only)
        self.connected = False
        self._stopping = False
        self._connections = 0        # bumped on every (re)connect
        self._acked = set()          # message ids the broker confirmed (QoS 1)
        self._subscribed_at = None   # time.monotonic() of the first SUBACK
        self._retained = None        # retained record from the broker (float), if any
        self._retained_seen = False
        # Latest swing from the paddle IMU (IMU_TOPIC). Replaced as a whole on
        # each event (one assignment, no lock), read by the game thread.
        self.last_swing = None       # game_state.Swing: arrival (monotonic) + peak (g)
        self.last_swing_info = None  # dict: peak, peak_gyro, delay_ms, count, wall time
        self.swing_count = 0

        # Touched ONLY by the game thread (see update())
        self.record_loaded = False
        self._broker_value = None    # what the broker retains, as far as we know
        self._sent = None            # (value, mid, connection number) of the last send
        self._last_best = 0
        self._reset_before_load = False
        self._last_game_publish = 0.0

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_connect_fail = self._on_connect_fail
        self.client.on_subscribe = self._on_subscribe
        self.client.on_message = self._on_message
        self.client.on_publish = self._on_publish
        self.client.reconnect_delay_set(config.MQTT_RECONNECT_MIN_S, config.MQTT_RECONNECT_MAX_S)
        # connect_async + loop_start: connecting (and every retry) happens in
        # paho's thread, so the game starts even with no network.
        self.client.connect_async(self.broker, self.port, keepalive=config.MQTT_KEEPALIVE_S)
        self.client.loop_start()

    # ------------------------------------------------------------ game -> MQTT
    def update(self, game: gs.GameState):
        """Call once per frame from the game loop. Never blocks."""
        if game.best_streak < self._last_best and not self.record_loaded:
            self._reset_before_load = True          # tag 5 before the record arrived
        if not self.record_loaded:
            self._try_load(game)                     # rule 1
        self._last_best = game.best_streak

        if self.record_loaded and self.publish_score:
            self._sync_record(float(game.best_streak))   # rule 2

        now = time.monotonic()
        if now - self._last_game_publish >= 1 / config.GAME_PUBLISH_HZ:
            self._last_game_publish = now
            self.publish_game(game)

    def status(self) -> str:
        """Short text for the screen, after "MQTT: connected/disconnected"."""
        if not self.record_loaded:
            return "loading record..." if self.connected else "record not loaded"
        rec = "none" if self._broker_value is None else f"{self._broker_value:g}"
        publishing = "publishing" if self.publish_score else f"not publishing ({self.why_not})"
        return f"record on broker {rec}, {publishing}"

    def publish_game(self, game: gs.GameState):
        if not self.connected:
            return   # nothing to do offline; the next message replaces this one anyway
        bx, by = game.ball_normalized()
        msg = {
            "state": game.state,
            "level": game.level,
            "streak": game.streak,
            "x": round(bx, 3),   # top view of the table: 0 = left, 1 = right
            "y": round(by, 3),   # 0 = opponent's end, 1 = my end
            "h": round(max(game.ball.y, 0.0), 3),   # ball height above the table (m)
        }
        self.client.publish(self.game_topic, json.dumps(msg), qos=0, retain=False)

    def stop(self):
        self._stopping = True
        self.client.disconnect()
        self.client.loop_stop()

    # ------------------------------------------------------------ record internals
    def _try_load(self, game: gs.GameState):
        """Rule 1: start best_streak from the retained all-time record."""
        if self._retained_seen:
            remote = self._retained
        elif (self._subscribed_at is not None
              and time.monotonic() - self._subscribed_at > config.RECORD_LOAD_WAIT_S):
            remote = None   # no retained value: the topic is empty
        else:
            return          # still waiting
        self.record_loaded = True
        self._broker_value = remote
        if remote is not None and not self._reset_before_load and remote > game.best_streak:
            game.best_streak = int(remote)
        print(f"MQTT: record loaded from {self.score_topic}: "
              f"{'none' if remote is None else remote}; best_streak = {game.best_streak}")

    def _sync_record(self, value: float):
        """Rule 2: make the broker's retained value equal `value` (QoS 1)."""
        if not self.connected:
            return   # offline: checked again every frame until the connection is back
        if value == self._broker_value:
            if self._sent is None or self._sent[0] != value:
                return                       # broker already has it (e.g. just loaded)
            _, mid, connection = self._sent
            if mid in self._acked or connection == self._connections:
                return                       # confirmed, or still in flight
            # else: connection dropped before the broker confirmed it: resend
        elif self._broker_value is None and value == 0:
            return                           # empty topic and no hits yet: nothing to say
        payload = str(value)                 # e.g. "12.0": just the float, nothing else
        info = self.client.publish(self.score_topic, payload, qos=1, retain=True)
        self._sent = (value, info.mid, self._connections)
        self._broker_value = value
        print(f"MQTT: record {payload} -> {self.score_topic} (retained)")

    # ------------------------------------------------------------ paho callbacks (flags only)
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if not reason_code.is_failure:
            self._connections += 1
            client.subscribe(self.score_topic, qos=1)   # to read the retained record
            client.subscribe(config.IMU_TOPIC)            # swing events from the paddle
        self.connected = not reason_code.is_failure
        print(f"MQTT: connect to {self.broker}:{self.port}: {reason_code}")

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties):
        if self._subscribed_at is None:
            self._subscribed_at = time.monotonic()

    def _on_message(self, client, userdata, msg):
        if msg.topic == config.IMU_TOPIC:
            self._on_swing(msg)
            return
        # Only the first retained message matters (the record at startup).
        # Later ones are our own publishes coming back, or a re-sent retained
        # value after a reconnect: the game is the source of truth by then.
        if msg.topic != self.score_topic or not msg.retain or self._retained_seen:
            return
        try:
            value = float(msg.payload.decode())
            if value < 0 or value != int(value):
                raise ValueError
            self._retained = value
        except ValueError:
            print(f"MQTT: ignoring unexpected retained value on {msg.topic}: {msg.payload!r}")
            self._retained = None   # treat as empty; the next record overwrites it
        self._retained_seen = True

    def _on_swing(self, msg):
        """A swing event from paddle_imu: {"swing": 1, "peak": g, "peak_gyro": dps, "t": board time}."""
        arrival = time.monotonic()
        wall = time.time()
        try:
            event = json.loads(msg.payload)
            if event.get("swing") != 1:
                return
            peak = float(event["peak"])
        except (ValueError, KeyError, TypeError):
            print(f"MQTT: ignoring unexpected message on {msg.topic}: {msg.payload[:100]!r}")
            return
        delay_ms = None
        try:
            delay_ms = (wall - float(event["t"])) * 1000   # board clock -> laptop clock
        except (KeyError, TypeError, ValueError):
            pass
        self.swing_count += 1
        self.last_swing_info = {"peak": peak, "peak_gyro": event.get("peak_gyro"),
                                "delay_ms": delay_ms, "count": self.swing_count, "arrival": arrival}
        self.last_swing = Swing(arrival=arrival, peak=peak)   # last: the game acts on this

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if self.connected and not self._stopping:
            print(f"MQTT: disconnected ({reason_code}); reconnecting automatically")
        self.connected = False

    def _on_connect_fail(self, client, userdata):
        self.connected = False

    def _on_publish(self, client, userdata, mid, reason_code, properties):
        self._acked.add(mid)   # set.add is atomic; no lock needed
