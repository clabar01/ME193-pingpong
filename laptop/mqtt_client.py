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
      plus "count": 10..1 during the EXTRA_LIFE countdown or 3, 2, 1 during the
      serve countdown (state READY), "final": the final volley count on the
      GAME_OVER screen, and "flash": "S"/"L" for CARD_FLASH_S after
      a SLOW-MO / EXTRA LIFE card activates, "GO" for SERVE_GO_SHOW_S at GO.
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

THE LOCAL BROKER (fast path for swings and game state, see config.py)
  - paho reconnects automatically after any drop (1 s, doubling to 10 s).
  - Keepalive LOCAL_KEEPALIVE_S (5 s): a dead link is noticed within ~8 s.
  - Every PING_PERIOD_S the game sends itself a message through each broker
    and times the echo. A local broker that hasn't echoed for
    LOCAL_PING_TIMEOUT_S counts as down even if paho still says connected.
  - Every drop is printed with its time and reason, every reconnect with how
    long it was down; summary() lists them at quit, with the measured delays.

Networking runs in paho's own background thread (loop_start), which also
reconnects automatically. Only the game thread calls publish(); paho's
callbacks just set flags (paho holds an internal lock while calling
on_publish, so sharing a lock with the game thread could deadlock the game).
"""
import json
import statistics
import threading
import time
import uuid
from collections import deque

import paho.mqtt.client as mqtt

import board_settings
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

        # FAST PATH: the local broker on the UNO Q (ArduinoApps/local_broker) for
        # swings (IMU_TOPIC, IMU_REJECTED_TOPIC) and game state (GAME_TOPIC). It
        # bridges those topics to hivemq, so if it's down everything still works
        # over hivemq (just slower). Swings are taken from whichever broker
        # delivers them first; the copy from the other one is dropped.
        self.local_host = (config.LOCAL_BROKER_HOST or board_settings.board_ip()
                           if config.LOCAL_BROKER_ENABLED else None)
        self.local_connected = False   # paho's view: TCP connection up
        # Delays and drops (lists: appended by paho threads, read at quit)
        self.swing_delays = {"local": [], "hivemq": []}   # ms, swing start (board clock) -> laptop
        self.ping_rtts = {"local": [], "hivemq": []}      # ms, laptop -> broker -> laptop
        self.drops = []                # dicts: broker, at, reason, down_s (None = still down)
        self._down_since = {}          # broker -> time.monotonic() it dropped
        self._session_start = time.monotonic()
        # Ping (health check): sent by the game thread, echoed back by the broker
        self._ping_topic = f"{config.PING_TOPIC}-{uuid.uuid4().hex[:8]}"
        self._ping_sent = {}           # (broker, seq) -> send time (dict ops are atomic)
        self._ping_seq = 0
        self._last_ping = 0.0
        self._local_echo = None        # time.monotonic() of the last local echo
        self.last_swing_via = None     # which broker delivered the latest swing
        self._seen = deque(maxlen=64)  # recent (topic, t, peak) keys, to drop bridge duplicates
        self._seen_lock = threading.Lock()   # only paho threads take it (never the game thread)
        self.last_rejected = None      # latest rejected move (Swing with .failed)
        self.local = None
        if self.local_host:
            self.local = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            self.local.on_connect = self._on_local_connect
            self.local.on_disconnect = self._on_local_disconnect
            self.local.on_message = self._on_local_message
            self.local.reconnect_delay_set(config.MQTT_RECONNECT_MIN_S, config.MQTT_RECONNECT_MAX_S)
            self.local.connect_async(self.local_host, config.LOCAL_BROKER_PORT,
                                     keepalive=config.LOCAL_KEEPALIVE_S)
            self.local.loop_start()

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
        if now - self._last_ping >= config.PING_PERIOD_S:
            self._last_ping = now
            self._send_pings(now)

    @property
    def local_ok(self) -> bool:
        """Local broker usable: connected AND echoing pings recently (a link
        can look connected for a while after the board or WiFi went away)."""
        if not self.local_connected:
            return False
        last = self._local_echo
        if last is None:   # just connected, first echo not back yet: give it time
            return True
        return time.monotonic() - last <= config.LOCAL_PING_TIMEOUT_S

    def fast_path(self) -> str:
        """Which broker carries swings and game state right now (for the screen)."""
        if self.local_ok:
            return f"local {self.local_host}"
        down = ""
        if self.local_host:
            since = self._down_since.get("local")
            down = (f" (local DOWN {time.monotonic() - since:.0f} s)" if since is not None
                    else " (local not answering)" if self.local_connected else " (local down)")
        if self.connected:
            return "hivemq" + down
        return "none" + down

    def status(self) -> str:
        """Short text for the screen, after "MQTT: connected/disconnected"."""
        fast = f"IMU/game via {self.fast_path()}"
        local_drops = sum(1 for d in self.drops if d["broker"] == "local")
        if local_drops:
            fast += f", local drops: {local_drops}"
        if not self.record_loaded:
            return f"{fast}  |  " + ("loading record..." if self.connected else "record not loaded")
        rec = "none" if self._broker_value is None else f"{self._broker_value:g}"
        publishing = "publishing" if self.publish_score else f"not publishing ({self.why_not})"
        return f"{fast}  |  record (hivemq) {rec}, {publishing}"

    def publish_game(self, game: gs.GameState):
        client = self.local if self.local_ok else self.client if self.connected else None
        if client is None:
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
        if game.state == gs.EXTRA_LIFE:
            msg["count"] = game.countdown_number      # 10..1: the LED matrix shows it
        elif game.state == gs.READY:
            msg["count"] = game.ready_number          # serve countdown 3, 2, 1
        elif game.state == gs.GAME_OVER:
            msg["final"] = game.last_rally            # the GAME OVER screen's final score
        flash = game.card_flash
        if flash is not None and time.monotonic() - flash[1] < config.CARD_FLASH_S:
            msg["flash"] = flash[0]                   # "S" / "L": a card just activated
        if game.go_at is not None and time.monotonic() - game.go_at < config.SERVE_GO_SHOW_S:
            msg["flash"] = "GO"                       # the serve countdown just reached GO
        client.publish(self.game_topic, json.dumps(msg), qos=0, retain=False)

    def summary(self) -> str:
        """Delays and connection drops for the end-of-session printout."""
        lines = []
        for via in ("local", "hivemq"):
            d = sorted(self.swing_delays[via])
            if d:
                lines.append(f"Swing delay via {via}: median {statistics.median(d):.0f} ms, "
                             f"range {d[0]:.0f}-{d[-1]:.0f} ms ({len(d)} swings; swing start on the "
                             f"board clock -> arrival on the laptop clock)")
            r = sorted(self.ping_rtts[via])
            if r:
                lines.append(f"Round trip laptop -> {via} broker -> laptop: median "
                             f"{statistics.median(r):.0f} ms, 95% under {r[int(len(r) * 0.95)]:.0f} ms, "
                             f"max {r[-1]:.0f} ms ({len(r)} pings)")
        minutes = (time.monotonic() - self._session_start) / 60
        if not self.drops:
            lines.append(f"No broker drops in {minutes:.1f} min.")
        for d in self.drops:
            down = "still down at quit" if d["down_s"] is None else f"down {d['down_s']:.1f} s"
            lines.append(f"Drop: {d['broker']} broker at {d['at']} ({d['reason']}), {down}")
        return "\n".join(lines)

    def stop(self):
        self._stopping = True
        for c in (self.client, self.local):
            if c is not None:
                c.disconnect()
                c.loop_stop()

    def _send_pings(self, now: float):
        """Game thread: one tiny message to ourselves through each connected
        broker; _on_ping times the echo. Old unanswered pings are dropped."""
        for key, t in list(self._ping_sent.items()):
            if now - t > 10:
                self._ping_sent.pop(key, None)
        self._ping_seq += 1
        for via, client, up in (("local", self.local, self.local_connected),
                                ("hivemq", self.client, self.connected)):
            if client is not None and up:
                self._ping_sent[(via, str(self._ping_seq))] = now
                client.publish(self._ping_topic, str(self._ping_seq), qos=0)

    def _on_ping(self, msg, via: str):
        sent = self._ping_sent.pop((via, msg.payload.decode(errors="replace")), None)
        if sent is None:
            return
        now = time.monotonic()
        self.ping_rtts[via].append((now - sent) * 1000)
        if via == "local":
            self._local_echo = now

    def _note_drop(self, broker: str, reason):
        self._down_since[broker] = time.monotonic()
        self.drops.append({"broker": broker, "at": time.strftime("%H:%M:%S"),
                           "reason": str(reason), "down_s": None})

    def _note_reconnect(self, broker: str):
        since = self._down_since.pop(broker, None)
        if since is None:
            return
        down = time.monotonic() - since
        for d in reversed(self.drops):
            if d["broker"] == broker and d["down_s"] is None:
                d["down_s"] = down
                break
        print(f"MQTT {broker}: back after {down:.1f} s down")

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
            client.subscribe(config.IMU_TOPIC)            # swing events (fallback path)
            client.subscribe(config.IMU_REJECTED_TOPIC)
            client.subscribe(self._ping_topic)
            self._note_reconnect("hivemq")
        self.connected = not reason_code.is_failure
        print(f"MQTT: connect to {self.broker}:{self.port}: {reason_code}")

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties):
        if self._subscribed_at is None:
            self._subscribed_at = time.monotonic()

    def _on_local_connect(self, client, userdata, flags, reason_code, properties):
        if self.local_connected and not reason_code.is_failure:
            # Reconnected without a disconnect callback: paho does that when the
            # socket fails inside select(). Still a drop: log it, timing it from
            # the last ping echo.
            self._note_drop("local", "connection lost")
            if self._local_echo is not None:
                self._down_since["local"] = self._local_echo
        self.local_connected = not reason_code.is_failure
        if self.local_connected:
            self._local_echo = None    # wait for the first echo on this connection
            client.subscribe(config.IMU_TOPIC)
            client.subscribe(config.IMU_REJECTED_TOPIC)
            client.subscribe(self._ping_topic)
            self._note_reconnect("local")
        print(f"MQTT local: connect to {self.local_host}:{config.LOCAL_BROKER_PORT}: {reason_code}")

    def _on_local_disconnect(self, client, userdata, flags, reason_code, properties):
        if self.local_connected and not self._stopping:
            # "Unspecified error" = the connection was lost (board rebooted or
            # lost power, WiFi dropped). paho reconnects by itself.
            print(f"MQTT local: DISCONNECTED at {time.strftime('%H:%M:%S')} ({reason_code}); "
                  f"using hivemq, reconnecting automatically")
            self._note_drop("local", reason_code)
        self.local_connected = False

    def _on_message(self, client, userdata, msg):
        if msg.topic == self._ping_topic:
            self._on_ping(msg, "hivemq")
            return
        if msg.topic in (config.IMU_TOPIC, config.IMU_REJECTED_TOPIC):
            self._on_imu(msg, "hivemq")
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

    def _on_local_message(self, client, userdata, msg):
        if msg.topic == self._ping_topic:
            self._on_ping(msg, "local")
        else:
            self._on_imu(msg, "local")

    def _on_imu(self, msg, via: str):
        """A swing (IMU_TOPIC) or rejected move (IMU_REJECTED_TOPIC) from
        paddle_imu: {"swing": 1|0, "peak": g, "peak_gyro": dps, "t": board time,
        ["failed": [...]]}. The same event can arrive from both brokers (the
        local one bridges to hivemq): only the first copy counts."""
        arrival = time.monotonic()
        wall = time.time()
        try:
            event = json.loads(msg.payload)
            peak = float(event["peak"])
        except (ValueError, KeyError, TypeError):
            print(f"MQTT: ignoring unexpected message on {msg.topic}: {msg.payload[:100]!r}")
            return
        key = (msg.topic, event.get("t"), peak)
        with self._seen_lock:
            if key in self._seen:
                return   # the other broker's copy, already handled
            self._seen.append(key)
        gyro = event.get("peak_gyro")
        gyro = float(gyro) if gyro is not None else None
        if msg.topic == config.IMU_REJECTED_TOPIC:
            self.last_rejected = Swing(arrival=arrival, peak=peak, peak_gyro=gyro,
                                       failed=list(event.get("failed", [])))
            return
        if event.get("swing") != 1:
            return
        self.last_swing_via = via
        delay_ms = None
        try:
            delay_ms = (wall - float(event["t"])) * 1000   # board clock -> laptop clock
        except (KeyError, TypeError, ValueError):
            pass
        self.swing_count += 1
        if delay_ms is not None:
            self.swing_delays[via].append(delay_ms)
            print(f"swing #{self.swing_count} via {via}: {peak:.2f} g, arrived {delay_ms:.0f} ms "
                  f"after it started on the board")
        self.last_swing_info = {"peak": peak, "peak_gyro": gyro, "via": via,
                                "delay_ms": delay_ms, "count": self.swing_count, "arrival": arrival}
        self.last_swing = Swing(arrival=arrival, peak=peak, peak_gyro=gyro)   # last: the game acts on this

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if self.connected and not self._stopping:
            print(f"MQTT hivemq: DISCONNECTED at {time.strftime('%H:%M:%S')} ({reason_code}); "
                  f"reconnecting automatically")
            self._note_drop("hivemq", reason_code)
        self.connected = False

    def _on_connect_fail(self, client, userdata):
        self.connected = False

    def _on_publish(self, client, userdata, mid, reason_code, properties):
        self._acked.add(mid)   # set.add is atomic; no lock needed
