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

# Record of continuous hits: published as a FLOAT, retained, ONLY when the
# record changes. Nothing else is ever published on this topic.
SCORE_TOPIC = "ME193/Rogers/CeciLaBarge"
IMU_TOPIC = "ME193/CeciLaBarge/imu"     # UNO Q -> laptop: swing events
GAME_TOPIC = "ME193/CeciLaBarge/game"   # laptop -> UNO Q: ball position + state

# ---------------------------------------------------------------- Window
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 720
FPS = 60

MAX_DT = 1 / 20     # cap one frame's time step (s) so a hiccup can't teleport the ball

# ---------------------------------------------------------------- Levels
# The AprilTag ID held up at the start selects the level (for now: number
# keys 0-2 on the WAITING screen).
#   ball_speed: pixels per second (the ball's speed never changes during a rally)
#   hit_zone:   width in pixels of the hit zone, centered on the paddle
#               (bigger = easier). The ball's center must be inside it.
LEVELS = {
    0: {"name": "Easy",   "ball_speed": 300, "hit_zone": 250},
    1: {"name": "Medium", "ball_speed": 450, "hit_zone": 180},
    2: {"name": "Hard",   "ball_speed": 600, "hit_zone": 120},
}
DEFAULT_LEVEL = 0   # used when starting from the keyboard (no tag)

# ---------------------------------------------------------------- Ball
BALL_RADIUS = 12
# A new ball is served from the far wall, aimed at the player, at a random
# angle up to this many degrees either side of straight down.
SERVE_ANGLE_DEG = 30
# After a hit the ball goes back toward the far wall. Hitting it off-center
# angles it, like real pong: at the edge of the hit zone the angle from
# straight up is this many degrees; at the center it goes straight up.
MAX_BOUNCE_ANGLE_DEG = 50

# ---------------------------------------------------------------- Paddle
# The player's side is the bottom of the screen; the paddle slides left/right.
PADDLE_WIDTH = 120
PADDLE_HEIGHT = 16
PADDLE_Y_FROM_BOTTOM = 60   # distance from the bottom edge to the paddle's top
PADDLE_KEY_SPEED = 700      # px/s when moving the paddle with the arrow keys

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

# ---------------------------------------------------------------- States
MISS_PAUSE_S = 1.0          # pause in MISS before the next serve
