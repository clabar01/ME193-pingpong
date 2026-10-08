"""All tunable values for the ping-pong game live here.

Other files import from this module instead of hard-coding numbers, so
tuning the game (speeds, thresholds, topics) only ever means editing this file.
Values below are placeholders until they are tuned on the real setup.
"""

# ---------------------------------------------------------------- MQTT
# Broker credentials (username/password), if any, go in board_secrets.py,
# which is git-ignored. Never put them here.
MQTT_BROKER = "broker.example.com"   # placeholder: replace with the class broker
MQTT_PORT = 1883

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

# ---------------------------------------------------------------- States
MISS_PAUSE_S = 1.0          # pause in MISS before the next serve
