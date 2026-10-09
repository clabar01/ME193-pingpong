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
# At startup the record is read from SCORE_TOPIC's retained value; if none
# arrives this long after subscribing, the topic counts as empty.
RECORD_LOAD_WAIT_S = 2.0

# Record of continuous hits: published as a FLOAT, retained, ONLY when the
# record changes. Nothing else is ever published on this topic.
SCORE_TOPIC = "ME193/Rogers/CeciLaBarge"
IMU_TOPIC = "ME193/CeciLaBarge/imu"     # UNO Q -> laptop: swing events
IMU_REJECTED_TOPIC = "ME193/CeciLaBarge/imu/rejected"   # UNO Q -> laptop: near-miss moves (tuning only)
IMU_RAW_TOPIC = "ME193/CeciLaBarge/imu/raw"   # UNO Q -> laptop, DEBUG only: raw samples (labeling)
GAME_TOPIC = "ME193/CeciLaBarge/game"   # laptop -> UNO Q: ball position + state

# ---------------------------------------------------------------- Window
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 720
FPS = 60

MAX_DT = 1 / 20     # cap one frame's time step (s) so a hiccup can't teleport the ball

# ---------------------------------------------------------------- Levels
# The AprilTag ID held up at the start selects the level (or keys 0-2 on the
# WAITING screen).
#   ball_speed:    m/s along the table for the opponent's shots (serve/returns)
#   hit_tolerance: m of slack around "paddle touches ball" (bigger = easier):
#                  a hit needs ball-to-paddle distance <= paddle radius + ball
#                  radius + this. Used with the keyboard paddle.
#   hit_tolerance_camera: the same for the camera paddle, about double: wrist
#                  tracking is noisier and laggier than arrow keys.
#   opponent:      the AI (see OPPONENT below): reaction delay (s) before it
#                  starts moving, max speed (m/s) across the table, aim error
#                  (m, random offset in where it thinks the ball will come)
#                  Miss rates in the comments: simulated over 400 of my returns.
LEVELS = {
    0: {"name": "Beginner", "ball_speed": 3.0, "hit_tolerance": 0.08, "hit_tolerance_camera": 0.16,
        "opponent": {"reaction_s": 0.28, "max_speed": 1.4, "aim_error_m": 0.10}},   # misses ~20%
    1: {"name": "Club",     "ball_speed": 3.8, "hit_tolerance": 0.05, "hit_tolerance_camera": 0.10,
        "opponent": {"reaction_s": 0.22, "max_speed": 1.8, "aim_error_m": 0.07}},   # misses ~9%
    2: {"name": "Pro",      "ball_speed": 4.6, "hit_tolerance": 0.025, "hit_tolerance_camera": 0.05,
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
PADDLE_Y_MIN_M = 0.05        # lowest / highest paddle height above the table
PADDLE_Y_MAX_M = 0.75
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
# visible (0..1). Below it the paddle freezes and "no hand" is shown.
MIN_WRIST_VISIBILITY = 0.5
# Smoothing (exponential moving average) of the paddle position:
#   paddle += SMOOTHING_ALPHA * (target - paddle)   every frame
# 1.0 = no smoothing (jittery but instant); smaller = smoother but laggier.
SMOOTHING_ALPHA = 0.35
# Calibration at startup (press C on the waiting screen to redo it):
# reach to your left edge, hold for the countdown, then the same on the right.
CALIB_COUNTDOWN_S = 3.0      # time to get into position before each sample
CALIB_SAMPLE_S = 0.5         # wrist x is averaged over this final stretch
CALIB_MIN_SPAN = 0.15        # left and right must differ by this much of the frame width
CAMERA_PREVIEW_WIDTH = 320   # size of the camera view drawn in the game window
# Paddle height from the wrist's height in the camera image (0 = top, 1 = bottom),
# no calibration: wrist at WRIST_Y_HIGH -> paddle at PADDLE_Y_MAX_M, wrist at
# WRIST_Y_LOW -> paddle at PADDLE_Y_MIN_M, linear in between (clamped).
WRIST_Y_HIGH = 0.25
WRIST_Y_LOW = 0.75

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
# Swing events from the paddle IMU arrive late: measured board -> laptop delay
# 110-485 ms, plus up to 60 ms of detection. So a swing is matched by its
# ARRIVAL time on the laptop, with a window that reaches mostly AFTER the
# moment the ball reaches the paddle line ("contact"):
#     contact - SWING_WINDOW_BEFORE_S  <=  swing arrival  <=  contact + SWING_WINDOW_AFTER_S
SWING_WINDOW_BEFORE_S = 0.15   # a swing may arrive this much before contact
SWING_WINDOW_AFTER_S = 0.60    # ...or this much after (485 ms worst delay + detection; a test
                               # swing sent 400 ms late arrived at 548 ms, so 0.55 was too tight)
# The paddle counts as on the ball if it was there at ANY moment from this
# long before contact up to contact: swinging moves the tracked wrist right
# at contact, so the position just before the swing is the one that counts.
PADDLE_LOOKBACK_S = 0.20
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
