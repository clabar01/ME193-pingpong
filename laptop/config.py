"""All tunable values for the ping-pong game live here.

Other files import from this module instead of hard-coding numbers, so
tuning the game (speeds, thresholds, topics) only ever means editing this file.
Values below are placeholders until they are tuned on the real setup.
"""

# ---------------------------------------------------------------- MQTT (mqtt_client.py)
# Same public broker as the door-to-door minifig project (no login needed).
# If a broker ever needs credentials, keep them out of this file and out of git.
MQTT_ENABLED = True                  # False = play fully offline (nothing published)
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_KEEPALIVE_S = 30                # broker notices a dead connection after ~1.5x this
MQTT_RECONNECT_MIN_S = 1             # automatic reconnect: wait 1 s, doubling up to...
MQTT_RECONNECT_MAX_S = 10            # ...10 s between attempts
GAME_PUBLISH_HZ = 10                 # game state messages per second on GAME_TOPIC
# Local broker (Mosquitto on the UNO Q, ArduinoApps/local_broker) for the fast
# topics: swings and game state. The record (SCORE_TOPIC) always uses MQTT_BROKER.
# Host None = the board's IP (BOARD_IP in ~/ArduinoApps/tools/board_secrets.py).
LOCAL_BROKER_ENABLED = True
LOCAL_BROKER_HOST = None
LOCAL_BROKER_PORT = 1883
# Finding the board: BOARD_IP first, then every other address in HOSTS of
# board_secrets.py (campus, home, AirFour.local), each given LOCAL_FIND_TIMEOUT_S
# to accept a connection on LOCAL_BROKER_PORT. The first one that answers is
# used (a warning says to update BOARD_IP if it wasn't BOARD_IP). None
# answering: try them all again every LOCAL_FIND_RETRY_S. Added 2026-10-12
# after a home session ran entirely over hivemq because BOARD_IP still had
# the 29BCNet address.
LOCAL_FIND_TIMEOUT_S = 1.0
LOCAL_FIND_RETRY_S = 5.0
# The local broker is on the same WiFi, so its connection can check itself
# often: with a 5 s keepalive a dead link is noticed (and reconnected) within
# ~8 s instead of ~45 s. Reconnecting is automatic (MQTT_RECONNECT_*).
LOCAL_KEEPALIVE_S = 5
# Health check + delay measurement: every PING_PERIOD_S the game sends a tiny
# message to itself through each broker and times the echo (round trip). A
# local broker that is "connected" but hasn't echoed for LOCAL_PING_TIMEOUT_S
# counts as down: game state and the swing window switch to hivemq until it
# answers again. The topic is outside ME193/CeciLaBarge/#, so the board's
# bridge doesn't copy it to hivemq.
PING_TOPIC = "ME193/ping/CeciLaBarge"
PING_PERIOD_S = 1.0
LOCAL_PING_TIMEOUT_S = 2.5
# At startup the record is read from SCORE_TOPIC's retained value; if none
# arrives this long after subscribing, the topic counts as empty.
RECORD_LOAD_WAIT_S = 2.0

# Record of continuous hits: published as a FLOAT, retained, ONLY when the
# record changes. Nothing else is ever published on this topic.
SCORE_TOPIC = "ME193/Rogers/CeciLaBarge"
IMU_TOPIC = "ME193/CeciLaBarge/imu"     # UNO Q -> laptop: swing events
IMU_REJECTED_TOPIC = "ME193/CeciLaBarge/imu/rejected"   # UNO Q -> laptop: moves that failed the swing checks
IMU_RAW_TOPIC = "ME193/CeciLaBarge/imu/raw"   # UNO Q -> laptop, DEBUG only: raw samples (labeling)
GAME_TOPIC = "ME193/CeciLaBarge/game"   # laptop -> UNO Q: ball position + state

# ---------------------------------------------------------------- Window
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 720
FPS = 60

MAX_DT = 1 / 20     # cap one frame's time step (s) so a hiccup can't teleport the ball
PHYSICS_DT = 1 / 240   # the ball moves in steps of at most this (s), whatever the frame rate

# ---------------------------------------------------------------- Levels
# The AprilTag ID held up at the start selects the level (or KEY_LEVELS on the
# WAITING screen).
# THE GOAL: as many volleys (my hits) in a row as possible. The opponent never
# misses, and every hit of mine makes the next ball a bit harder (RALLY RAMP
# below); a miss resets everything to the level's starting values.
#   start_speed:   m/s along the table of the first ball of a rally (all
#                  shots, mine and the opponent's, use the current speed)
#   speed_ramp:    the speed grows by this fraction of start_speed per hit
#                  (0.02 = +2% per hit, so x1.20 after 10 hits)...
#   speed_max:     ...up to this many m/s (stays humanly hittable)
#   arc_speed:     the slowest speed a shot's ARC is computed for. A slower
#                  ball would have to fly much higher to clear the net (at
#                  2.4 m/s Beginner balls reached my plane up to 1.1 m high,
#                  above the paddle's reach), so below arc_speed the ball
#                  keeps the arc of an arc_speed shot and the game plays it
#                  in slow motion instead: it still moves at the slower speed.
#   hit_tolerance: (left/right, up/down) slack in m around "paddle touches
#                  ball" (bigger = easier). A hit needs the ball's center within
#                  paddle radius + ball radius + this of the paddle's center,
#                  checked SEPARATELY across (x) and in height (y): a box, not
#                  a circle. None for up/down = height is not checked at all.
#                  Used with the keyboard paddle.
#   hit_tolerance_camera: the same for the camera paddle, wider: wrist
#                  tracking is noisier and laggier than arrow keys.
#   Beginner: left/right only, generous. Club: both, loose. Pro: both, tight.
#   Height tolerances are smaller than left/right ones because balls reach my
#   plane within a narrow height band per level (Beginner 0.36-0.66 m, Club
#   0.27-0.46, Pro 0.26-0.36): a looser height check would never fail.
#   (the hit tolerances shrink as the speed ramps up, see ZONE_SHRINK_*)
#   opponent:      how the AI moves (looks only: it never misses): reaction
#                  delay (s) before it starts sliding toward the ball, and its
#                  usual top speed (m/s); it goes faster when it has to, so it
#                  always gets there.
LEVELS = {
    0: {"name": "Beginner", "start_speed": 2.4, "speed_ramp": 0.02, "speed_max": 4.8, "arc_speed": 3.0,
        "hit_tolerance": (0.12, None), "hit_tolerance_camera": (0.20, None),
        "opponent": {"reaction_s": 0.28, "max_speed": 1.4}},
    1: {"name": "Club",     "start_speed": 3.0, "speed_ramp": 0.025, "speed_max": 5.6, "arc_speed": 3.8,
        "hit_tolerance": (0.07, 0.05), "hit_tolerance_camera": (0.12, 0.08),
        "opponent": {"reaction_s": 0.22, "max_speed": 1.8}},
    2: {"name": "Pro",      "start_speed": 3.7, "speed_ramp": 0.03, "speed_max": 6.4, "arc_speed": 4.6,
        "hit_tolerance": (0.03, 0.02), "hit_tolerance_camera": (0.05, 0.03),
        "opponent": {"reaction_s": 0.16, "max_speed": 2.2}},
}
# ---------------------------------------------------------------- Rally ramp (all levels)
# The opponent aims its shots within +-spread m across my side; the spread
# starts narrow and widens with every hit of mine, up to the table's usable width.
AIM_SPREAD_START_M = 0.15
AIM_SPREAD_PER_HIT_M = 0.025
AIM_SPREAD_MAX_M = 0.55
# The hit zone's tolerances shrink as the ball speeds up:
#   tolerance x max(ZONE_MIN_SCALE, 1 - ZONE_SHRINK_PER_SPEED x (speed factor - 1))
# e.g. at x1.4 speed the tolerances are 80% of the level's, never below 60%.
# (Only the tolerance shrinks, not the paddle itself.)
ZONE_SHRINK_PER_SPEED = 0.5
ZONE_MIN_SCALE = 0.6
DEFAULT_LEVEL = 0   # used when starting from the keyboard (no tag)
# Keyboard level keys on the WAITING and GAME OVER screens: number key -> level
# (1 = Beginner, 2 = Club, 3 = Pro). They only pick the level; Space starts.
# The AprilTag level cards (TAG_LEVELS, IDs 0/1/2) work as before.
KEY_LEVELS = {1: 0, 2: 1, 3: 2}

# ---------------------------------------------------------------- Table & world (meters)
# x: across the table (0 = center, + = right), z: along it (0 = MY end,
# TABLE_LENGTH = the opponent's end), y: height above the table surface.
TABLE_LENGTH = 2.74
TABLE_WIDTH = 1.525
NET_HEIGHT = 0.1525
NET_CLEARANCE = 0.05     # every shot is aimed to pass at least this far above the net
GRAVITY = 9.81           # m/s^2
BOUNCE_RESTITUTION = 0.85   # fraction of vertical speed kept in a bounce on the table
BALL_RADIUS_M = 0.025    # a little bigger than a real ball (0.02) so it's easy to see

# Hitting planes: where each player's paddle meets the ball (behind each end)
MY_HIT_Z = -0.20
OPP_HIT_Z = TABLE_LENGTH + 0.20

# Where shots land: opponent shots land on my side in this depth range...
MY_LANDING_Z = (0.35, 1.00)
# ...and mine land on the opponent's side: depth goes from shallow (soft swing)
# to deep (hard swing).
OPP_LANDING_Z_SOFT = TABLE_LENGTH / 2 + 0.30
OPP_LANDING_Z_HARD = TABLE_LENGTH - 0.15
LANDING_X_MAX = 0.55     # my returns land within +-this across the opponent's side
SERVE_HEIGHT = 0.30      # opponent serves from this height above the table

# ---------------------------------------------------------------- View (first person)
# A pinhole camera at my end of the table, looking straight along it.
VIEW_CAM_X = 0.0
VIEW_CAM_Y = 0.55        # eye height above the table (m)
VIEW_CAM_Z = -1.20       # behind my end (m)
VIEW_FOCAL_PX = 940      # bigger = more zoomed in
VIEW_HORIZON_Y = 220     # screen y of the horizon (where the camera looks)

# ---------------------------------------------------------------- My paddle
# The paddle moves in my hitting plane (z = MY_HIT_Z): x across, y = height.
PADDLE_RADIUS_M = 0.08
PADDLE_X_RANGE_M = 0.65      # x from -this to +this (keeps the paddle on screen)
PADDLE_Y_MIN_M = 0.15        # lowest / highest paddle height above the table (balls
PADDLE_Y_MAX_M = 0.75        # reach my plane at 0.26-0.66 m on all levels)
# Target marker: where the ball will cross my hitting plane. A clear marker
# appears when the ball bounces on my side; that is only 0.13-0.42 s before
# contact, so with TARGET_EARLY a faint predicted marker is shown from the
# moment the opponent hits the ball.
TARGET_EARLY = True
PADDLE_KEY_SPEED = 1.2       # m/s with the arrow keys (left/right/up/down)

# ---------------------------------------------------------------- Opponent
OPP_HIT_HEIGHT = 0.30    # height its paddle is drawn at

# ---------------------------------------------------------------- Camera paddle (pose_input.py)
# Choose with:  python laptop/game.py --input camera   (keyboard is the default)
CAMERA_INDEX = 0             # 0 = built-in webcam; try 1 for an external camera
CAMERA_WIDTH = 640           # requested capture size (smaller = faster pose detection)
CAMERA_HEIGHT = 480
POSE_MODEL_PATH = "assets/models/pose_landmarker_lite.task"   # relative to the repo root

# --- Pick your opponent (minifig_input.py, opponent.py) ---------------------
# Before the level select, hold a blue or green minifig up to the camera. The
# detector is the YOLOv8n trained in the door-to-door project (mini-fig-yolo,
# models/best.pt), converted once to ONNX (tools/export_minifig_onnx.py), run
# with OpenCV DNN only on that screen.
MINIFIG_MODEL_PATH = "assets/models/minifig_yolov8n.onnx"
MINIFIG_IMG_SIZE = 640       # the size the model was trained / exported at
MINIFIG_CONF = 0.5           # same threshold as detect_live.py
MINIFIG_HZ = 8               # detections per second on the select screen (camera thread)
MINIFIG_HOLD_S = 1.0         # a color must be seen this long to count...
MINIFIG_GAP_S = 0.35         # ...forgiving gaps (missed frames) up to this long
OPPONENT_CHOSEN_S = 1.6      # "BLUE chosen!" + preview, then on to calibration / levels
OPPONENT_KEYS = {"b": "blue", "g": "green"}
OPPONENT_DEFAULT = None      # "blue" / "green" = skip the select screen (or game.py --opponent)
TRACK_WRIST = "right"        # "right" or "left": which of YOUR wrists moves the paddle
# A wrist counts as detected only if MediaPipe is at least this confident it's
# visible (0..1). The real paddle in the hand often hides the wrist, so below
# it the hand position is estimated from other landmarks (pose_input.py):
#   1. index/pinky knuckles (if visible), or
#   2. the elbow plus the last seen elbow->wrist offset (the forearm).
# Each fallback adds the offset to the wrist measured the last time the wrist
# itself was visible, so switching sources doesn't make the paddle jump.
MIN_WRIST_VISIBILITY = 0.5
FALLBACK_MIN_VISIBILITY = 0.5   # same test for the knuckles / elbow
# Nothing usable at all: the paddle holds its last position this long (still
# counts as tracked), then tracking is "lost" (paddle frozen, "no hand").
TRACK_HOLD_S = 0.3
# A hand found within this (fraction of the frame) of an AprilTag in view is
# the free hand holding a card, not the paddle hand: ignored (paddle holds).
CARD_GUARD_MARGIN = 0.10
# Smoothing (exponential moving average) of the paddle position:
#   paddle += SMOOTHING_ALPHA * (target - paddle)   every frame
# 1.0 = no smoothing (jittery but instant); smaller = smoother but laggier.
SMOOTHING_ALPHA = 0.35
# Calibration at startup (press C on the waiting screen to redo it): reach to
# your left edge, hold for the countdown, then right, then high, then low.
CALIB_COUNTDOWN_S = 3.0      # time to get into position before each sample
CALIB_SAMPLE_S = 0.5         # wrist x is averaged over this final stretch
CALIB_MIN_SPAN = 0.35        # left and right must differ by at least this much of the frame
                             # width; narrower = tiny wrist moves cross the whole table
CALIB_MIN_SPAN_Y = 0.20      # high and low: at least this much of the frame height
CAMERA_PREVIEW_WIDTH = 320   # camera view on the start / calibration screens (and in debug)
CAMERA_PREVIEW_SMALL_WIDTH = 176   # ...tucked into the bottom-left corner during play
# Paddle height: wrist at the calibrated HIGH spot -> PADDLE_Y_MAX_M, at the
# LOW spot -> PADDLE_Y_MIN_M, linear in between (clamped).

# ---------------------------------------------------------------- AprilTags (apriltag_input.py)
# Detected with OpenCV's aruco module, as in the AprilTag parking project.
# Active with --input camera (or --tags with the keyboard paddle).
# Print the tags with:  python tools/make_tags.py
TAG_FAMILY = "tag36h11"
TAG_LEVELS = {0: 0, 1: 1, 2: 2}   # tag ID -> level (key of LEVELS)
TAG_RESET_BEST = 5                # tag ID that resets best_streak
# Level and reset tags only act on the WAITING screen: during a game (playing,
# the MISS pause, the extra-life countdown) they are ignored, so a stray card
# can't change the level or wipe the record.
# A tag must be seen continuously this long to act (start / reset), so a
# tag that's just passing by the camera does nothing.
TAG_HOLD_S = 1.0
# Detection flickers: a tag that drops out for less than this still counts as
# "continuously seen".
TAG_DROPOUT_S = 0.25

# ---------------------------------------------------------------- Power-up cards (AprilTags)
# Each card works once per RALLY: both come back whenever a rally ends (my
# miss, after any extra-life countdown) and at the start of a game, so every
# new rally starts with both. Showing a used card only shows "already used".
# Keyboard stand-ins for testing: S = slow-mo, L = extra life.
POWERUP_HOLD_S = 0.3         # a card must be held this long to act (shorter than TAG_HOLD_S)
CARD_NOTE_S = 1.5            # "already used" / "not now" messages stay this long
CARD_FLASH_S = 1.0           # the LED matrix shows S / L this long when a card activates
# Tag 10 = SLOW-MO: only while PLAYING. The ball (and the opponent) move at
# SLOWMO_SPEED x the current, ramped-up speed for the next SLOWMO_BALLS balls
# that reach my paddle (the ball in the air when it's shown counts as the
# first if it's coming to me), then everything is back at the ramp's speed.
# The swing timing window stays in real time.
TAG_SLOWMO = 10
SLOWMO_SPEED = 0.5
SLOWMO_BALLS = 3
# Tag 11 = EXTRA LIFE: when I miss and it's unused, the game pauses with a
# countdown; showing the card before it runs out undoes the miss (the volley
# count and the ramp continue, a new ball is served). Once used, misses end
# the rally at once.
TAG_EXTRA_LIFE = 11
EXTRA_LIFE_COUNTDOWN_S = 5.0

# ---------------------------------------------------------------- Swing timing (is_hit)
# A swing is matched by its ARRIVAL time on the laptop, in a window around the
# moment the ball reaches my hitting plane ("contact"):
#     contact - before  <=  swing arrival  <=  contact + after
# given as (before, after) in seconds. Which window applies is decided at
# contact, from how swings are arriving right then:
#   FAST: swings via the board's local broker (measured 20 ms median on
#         29BCNet), or Space / --no-imu (instant). Centered on contact.
#   SLOW: IMU swings via broker.hivemq.com when the local broker is down
#         (measured 110-485 ms on campus, 0.7-4 s on 29BCNet): shifted late.
SWING_WINDOW_FAST = (0.35, 0.35)
SWING_WINDOW_SLOW = (0.15, 0.60)
# The paddle counts as on the ball if it was there at ANY moment from this
# long before contact up to contact: swinging moves the tracked wrist right
# at contact, so the position just before the swing is the one that counts.
PADDLE_LOOKBACK_S = 0.20
# Diagnostics only (miss reasons, not the rule): a swing up to this long before
# the window opens is reported as "too early"; after a timing miss the game
# keeps watching this long after contact for a "too late" swing.
EARLY_SWING_LOOK_S = 1.0
LATE_SWING_WATCH_S = 1.5
# While waiting for a late swing, the ball presses into the paddle: it keeps
# its direction but slows down with this time constant (s)...
HOLD_DECAY_S = 0.04
HOLD_MAX_SINK_M = 0.04   # ...and goes at most this far past my hitting plane

# ---------------------------------------------------------------- Return speed (harder swing = faster)
# After a hit the ball goes back at the rally's current speed times a factor set
# by the swing's peak acceleration: RETURN_FACTOR_MIN at or below
# RETURN_PEAK_LOW_G, RETURN_FACTOR_MAX at or above RETURN_PEAK_HIGH_G, linear
# in between. The same factor also sets how DEEP the return lands on the
# opponent's side (OPP_LANDING_Z_SOFT .. _HARD). The opponent's shots use the
# current speed without a factor. Hits without a measured peak use 1.0.
RETURN_PEAK_LOW_G = 2.2        # = the IMU's MIN_ACCEL_PEAK_G: the softest swing that counts
RETURN_PEAK_HIGH_G = 5.5       # about the hardest swings in the hand tests
RETURN_FACTOR_MIN = 0.8
RETURN_FACTOR_MAX = 1.6

# ---------------------------------------------------------------- Swing meter (draw.py)
SWING_METER_MAX_G = 6.0        # top of the meter
SWING_METER_FLASH_S = 0.5      # how long the meter flashes after a swing event

# ---------------------------------------------------------------- Score display (draw.py)
SCORE_POP_S = 0.3              # the volley count pops (grows, then settles) this long on each hit
# One message at a time: GO!, HIGH SCORE!, SLOW-MO!, EXTRA LIFE!, ALREADY USED
# and the miss word share one banner; a new one replaces the old. Each pops in
# and fades out over BANNER_S.
BANNER_S = 1.0
# The miss word ("TOO LATE", "MISSED", "SWING!") shows this long on its own
# before the EXTRA LIFE countdown or GAME OVER screen appears.
MISS_FLASH_S = 0.9
# Debug overlay (MQTT, swing meter, delays, tracking, sound trace, miss
# details, speed): hidden by default; D toggles it, --debug starts with it on.
DEBUG_OVERLAY = False

# ---------------------------------------------------------------- Sounds (sounds.py)
# Which sound when: see the table at the top of sounds.py. Files in
# assets/sounds/ (hit, tick, record, death, slowmo, life, count, go, smash .wav); missing
# ones are synthesized.
SOUND_MUTED = False          # True = no sounds at all (no audio device is opened)
SOUND_VOLUME = 0.5           # 0..1
SOUND_SAMPLE_RATE = 44100    # used if the speaker supports it, else its own rate
# Passing the record gets the "record" chime + HIGH SCORE banner only if the
# volley count is at least this (so a fresh session doesn't celebrate hit 1).
MIN_RECORD_FOR_HIGH_SCORE = 3
SOUND_SHOW_S = 2.5           # each sound's name + reason stays on screen this long

# ---------------------------------------------------------------- Voice commands (voice.py, speech.py)
# Offline speech recognition with Vosk (small English model, models/ - see
# README). The recognizer only knows VOICE_COMMANDS; everything else comes
# out as "unknown" and is ignored. Turn off with --no-voice.
VOICE_ENABLED = True
VOICE_MODEL_PATH = "models/vosk-model-small-en-us-0.15"   # relative to the repo root
VOICE_COMMANDS = ["smash", "play again", "level one", "level two", "level three",
                  "pause", "resume", "score", "blue", "green"]
VOICE_SAMPLE_RATE = 16000     # what the model expects (the mic is resampled if needed)
VOICE_COOLDOWN_S = 1.0        # the same command can't trigger twice within this
# A command counts only if the grammar recognizer heard exactly that phrase
# (nothing else in the utterance). The STRICT commands, which would disrupt
# the game if they fired by mistake, must ALSO be heard by a second, free
# recognizer on the same audio: every word at least VOICE_WORD_MATCH similar
# (difflib ratio), so near-misses like "play the game" -> "play again" don't
# fire. The others (smash, pause, resume, score) skip that check, so they
# still work in a noisy room; a wrong pause is harmless (say "resume"). (pause
# was strict until a test where the check heard "pass" and dropped it.) Measured on synthesized speech + classroom babble: strict
# = 0 false triggers in 2 min of chatter but 22% recognized at 10 dB SNR;
# grammar only = 78% at 10 dB but 5 false triggers (all level / play again).
VOICE_STRICT_COMMANDS = ["play again", "level one", "level two", "level three"]
VOICE_WORD_MATCH = 0.75
# The game's own sounds must not trigger commands: the mic is ignored while a
# sound at least VOICE_MUTE_MIN_SOUND_S long is playing (death, jingles, GO,
# smash...; not the short tick/tock/count beeps) or the laptop is speaking,
# plus VOICE_MUTE_TAIL_S after.
VOICE_MUTE_MIN_SOUND_S = 0.15
VOICE_MUTE_TAIL_S = 0.4
VOICE_SHOW_S = 1.5            # the last heard command stays on screen this long
TTS_RATE = 190                # words per minute for "score" (pyttsx3)
# "smash" (voice power-up): said any time during a rally, it arms a SMASH for
# the NEXT ball that reaches my paddle; hitting it sends the return back
# SMASH_SPEED_FACTOR x faster. Once per rally (comes back with the power-up
# cards); a missed armed ball just uses it up.
SMASH_SPEED_FACTOR = 1.5

# ---------------------------------------------------------------- States
# When a rally ends: the GAME OVER screen (final volley count, best, NEW HIGH
# SCORE!). Play again with a swing (same level), a level tag (that level) or
# Space; swings and Space are ignored for the first GAME_OVER_SWING_IGNORE_S
# (the last swing's follow-through). Nobody plays for GAME_OVER_IDLE_S ->
# back to the WAITING screen.
GAME_OVER_SWING_IGNORE_S = 2.0
GAME_OVER_IDLE_S = 60.0
# Before EVERY serve (game start, playing again, after an extra life): a big
# "3, 2, 1, GO!" on screen and the LED matrix, a beep per number and a
# higher beep on GO. The ball is served at GO.
SERVE_COUNT_FROM = 3        # counts 3, 2, 1
SERVE_COUNT_STEP_S = 1.0    # seconds per number
SERVE_GO_SHOW_S = 0.6       # "GO!" stays on screen this long (the ball is already flying)
