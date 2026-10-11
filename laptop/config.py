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
# The AprilTag ID held up at the start selects the level (or keys 0-2 on the
# WAITING screen).
#   ball_speed:    m/s along the table for the opponent's shots (serve/returns)
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
#   opponent:      the AI (see OPPONENT below): reaction delay (s) before it
#                  starts moving, max speed (m/s) across the table, aim error
#                  (m, random offset in where it thinks the ball will come)
#                  Miss rates in the comments: simulated over 400 of my returns.
LEVELS = {
    0: {"name": "Beginner", "ball_speed": 3.0, "hit_tolerance": (0.12, None), "hit_tolerance_camera": (0.20, None),
        "opponent": {"reaction_s": 0.28, "max_speed": 1.4, "aim_error_m": 0.10}},   # misses ~20%
    1: {"name": "Club",     "ball_speed": 3.8, "hit_tolerance": (0.07, 0.05), "hit_tolerance_camera": (0.12, 0.08),
        "opponent": {"reaction_s": 0.22, "max_speed": 1.8, "aim_error_m": 0.07}},   # misses ~9%
    2: {"name": "Pro",      "ball_speed": 4.6, "hit_tolerance": (0.03, 0.02), "hit_tolerance_camera": (0.05, 0.03),
        "opponent": {"reaction_s": 0.16, "max_speed": 2.2, "aim_error_m": 0.06}},   # misses ~3%
}
DEFAULT_LEVEL = 0   # used when starting from the keyboard (no tag)

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
LANDING_X_MAX = 0.55     # shots land within +-this across the table
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
OPP_REACH_M = 0.12       # the opponent returns the ball if its paddle is within this (x) at its plane
OPP_HIT_HEIGHT = 0.30    # height its paddle is drawn at
POINT_PAUSE_S = 1.0      # pause after the opponent misses, before the next serve

# ---------------------------------------------------------------- Camera paddle (pose_input.py)
# Choose with:  python laptop/game.py --input camera   (keyboard is the default)
CAMERA_INDEX = 0             # 0 = built-in webcam; try 1 for an external camera
CAMERA_WIDTH = 640           # requested capture size (smaller = faster pose detection)
CAMERA_HEIGHT = 480
POSE_MODEL_PATH = "assets/models/pose_landmarker_lite.task"   # relative to the repo root
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
CAMERA_PREVIEW_WIDTH = 320   # size of the camera view drawn in the game window
# Paddle height: wrist at the calibrated HIGH spot -> PADDLE_Y_MAX_M, at the
# LOW spot -> PADDLE_Y_MIN_M, linear in between (clamped).

# ---------------------------------------------------------------- AprilTags (apriltag_input.py)
# Detected with OpenCV's aruco module, as in the AprilTag parking project.
# Active with --input camera (or --tags with the keyboard paddle).
# Print the tags with:  python tools/make_tags.py
TAG_FAMILY = "tag36h11"
TAG_LEVELS = {0: 0, 1: 1, 2: 2}   # tag ID -> level (key of LEVELS)
TAG_RESET_BEST = 5                # tag ID that resets best_streak
# A tag must be seen continuously this long to act (start / reset), so a
# tag that's just passing by the camera does nothing.
TAG_HOLD_S = 1.0
# Detection flickers: a tag that drops out for less than this still counts as
# "continuously seen".
TAG_DROPOUT_S = 0.25

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
# After a hit the ball goes back at the level's ball_speed times a factor set
# by the swing's peak acceleration: RETURN_FACTOR_MIN at or below
# RETURN_PEAK_LOW_G, RETURN_FACTOR_MAX at or above RETURN_PEAK_HIGH_G, linear
# in between. The same factor also sets how DEEP the return lands on the
# opponent's side (OPP_LANDING_Z_SOFT .. _HARD). The opponent's shots always
# use the level's speed. Hits without a measured peak use 1.0.
RETURN_PEAK_LOW_G = 2.2        # = the IMU's MIN_ACCEL_PEAK_G: the softest swing that counts
RETURN_PEAK_HIGH_G = 5.5       # about the hardest swings in the hand tests
RETURN_FACTOR_MIN = 0.8
RETURN_FACTOR_MAX = 1.6

# ---------------------------------------------------------------- Swing meter (draw.py)
SWING_METER_MAX_G = 6.0        # top of the meter
SWING_METER_FLASH_S = 0.5      # how long the meter flashes after a swing event

# ---------------------------------------------------------------- States
MISS_PAUSE_S = 1.0          # pause in MISS before the next serve
