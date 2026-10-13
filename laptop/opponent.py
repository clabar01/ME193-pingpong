"""The opponent character: a blocky toy figure at the far end of the table.

Original design (no logos or branding): a square head, a box body, box arms
and legs, mitten hands, holding a paddle, in the color of the minifig picked
on the select screen (minifig_input.py). Everything here is COSMETIC: it only
reads the game state and never changes it, so it can't affect the hit rule or
any timing (game_state.py doesn't know it exists).

MOTION
  A pose = the positions of 15 joints (JOINTS) relative to the hip center, in
  table axes: x = to MY right, y = up, z = away from me (the figure faces me).
  Motions are short clips of poses:
    idle      looped while waiting
    sidestep  leg cycle, played while the figure slides sideways
    forehand  one or more takes of a swing, each with its CONTACT frame
    backhand  (the moment the paddle should meet the ball)
  Clips recorded with tools/record_swing.py (MediaPipe world landmarks of a
  real person doing the motion) are loaded from assets/motions/*.json;
  any clip that isn't there is made procedurally from keyframes below, so
  the same states always work.

  Retargeting: the recorded person and the figure have different proportions,
  so only each bone's DIRECTION is taken from the pose; the figure's own bone
  lengths (BONES) are applied from the hips outward.

THE STATES (OpponentFigure.update, every frame)
  - idle sway in the color's personality (blue: small, calm; green: bouncy)
  - side step: the body follows the game's Opponent AI (its paddle x), and
    the legs step in proportion to how fast it slides
  - swing: when the ball comes toward the figure, forehand if it will arrive
    on the paddle-hand side, else backhand. The clip time is computed from
    the BALL itself every frame: time to contact = distance to the
    opponent's hitting plane / the ball's speed along the table, so the
    stroke stays in sync even in slow-mo. The wind-up blends in before
    contact, the clip's contact frame plays exactly when the game returns the
    ball, then the follow-through. At contact the arm is nudged (smoothly,
    over the last 0.4 s) so the paddle center is exactly at the ball. Serves
    work the same way, with GO as the contact moment.
  - reaction when I miss: green celebrates (jumps, arms up), blue nods.
"""
import json
import math
import random
from pathlib import Path

import numpy as np
import pygame

import config

REPO_ROOT = Path(__file__).resolve().parent.parent
MOTION_DIR = REPO_ROOT / "assets" / "motions"
MOTION_NAMES = ("idle", "sidestep", "forehand", "backhand")
CLIP_FPS = 30

# The figure's left (l_) is at +x: it faces me, so its left is on my right.
JOINTS = ["hip", "l_hip", "r_hip", "l_kn", "r_kn", "l_an", "r_an",
          "neck", "l_sh", "r_sh", "l_el", "r_el", "l_wr", "r_wr", "head"]
J = {name: i for i, name in enumerate(JOINTS)}
UPPER = [J[n] for n in ("neck", "l_sh", "r_sh", "l_el", "r_el", "l_wr", "r_wr", "head")]
LEGS = [J[n] for n in ("l_hip", "r_hip", "l_kn", "r_kn", "l_an", "r_an")]
ARMS = [J[n] for n in ("l_sh", "r_sh", "l_el", "r_el", "l_wr", "r_wr")]

# Figure proportions (m): a sturdy toy figure about 1.7 m tall with a big head.
# (parent, child, length): parents come first, the hip center is the root.
BONES = [("hip", "l_hip", 0.11), ("hip", "r_hip", 0.11),
         ("l_hip", "l_kn", 0.40), ("l_kn", "l_an", 0.40),
         ("r_hip", "r_kn", 0.40), ("r_kn", "r_an", 0.40),
         ("hip", "neck", 0.52), ("neck", "l_sh", 0.22), ("neck", "r_sh", 0.22),
         ("l_sh", "l_el", 0.27), ("l_el", "l_wr", 0.25),
         ("r_sh", "r_el", 0.27), ("r_el", "r_wr", 0.25),
         ("neck", "head", 0.21)]
BONE_IDX = [(J[a], J[b], n) for a, b, n in BONES]
HEAD_SIZE = 0.32
HIP_HEIGHT = 0.80 - 0.76          # hip center above the table top (floor is 0.76 m below it)
PADDLE_REACH = 0.15               # paddle center beyond the wrist, along the forearm

PERSONALITIES = {
    # amp: how much bigger the swing is than the recording (wind-up and follow-through)
    "blue": {"body": (60, 115, 225), "legs": (40, 75, 160), "amp": 0.9, "sway": 0.6, "bob": 0.0,
             "hair": "flat", "reaction": "nod", "name": "BLUE", "trait": "calm and steady"},
    "green": {"body": (55, 180, 95), "legs": (35, 120, 65), "amp": 1.3, "sway": 1.5, "bob": 0.025,
              "hair": "spiky", "reaction": "celebrate", "name": "GREEN", "trait": "flashy"},
}
SKIN = (240, 202, 150)
PADDLE_RUBBER = (215, 60, 70)
PADDLE_HANDLE = (120, 80, 50)
OUTLINE = (20, 24, 34)
LIGHT = np.array([0.35, 0.55, -0.75]) / np.linalg.norm([0.35, 0.55, -0.75])


def smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


# ------------------------------------------------------------ procedural clips
def _pose(**joints) -> np.ndarray:
    p = REST.copy()
    for name, xyz in joints.items():
        p[J[name]] = xyz
    return p


REST = np.array([
    (0, 0, 0), (0.11, 0, 0), (-0.11, 0, 0),                 # hips
    (0.16, -0.39, -0.06), (-0.16, -0.39, -0.06),            # knees, a little bent, apart
    (0.19, -0.78, 0.02), (-0.19, -0.78, 0.02),              # ankles
    (0, 0.51, -0.06), (0.22, 0.49, -0.06), (-0.22, 0.49, -0.06),   # neck, shoulders (leaning in)
    (0.30, 0.27, -0.14), (-0.30, 0.27, -0.14),              # elbows forward
    (0.18, 0.20, -0.36), (-0.22, 0.20, -0.38),              # hands in front, ready
    (0, 0.71, -0.10)], float)                               # head


def _rotate_y(points: np.ndarray, angle: float) -> np.ndarray:
    """Turn the upper body: angle > 0 swings the right shoulder (-x) back (+z)."""
    c, s = math.cos(angle), math.sin(angle)
    out = points.copy()
    out[:, 0] = points[:, 0] * c + points[:, 2] * s
    out[:, 2] = -points[:, 0] * s + points[:, 2] * c
    return out


def _stroke_key(turn: float, wrist, elbow=None, lift=0.0) -> np.ndarray:
    """A swing keyframe: the upper body turned by `turn` (rad), the paddle
    (right) hand at `wrist`, its elbow at `elbow` (default: between shoulder
    and hand, pushed out)."""
    p = REST.copy()
    p[UPPER] = _rotate_y(REST[UPPER], turn)
    p[UPPER, 1] += lift
    w = np.array(wrist, float)
    p[J["r_wr"]] = w
    sh = p[J["r_sh"]]
    p[J["r_el"]] = np.array(elbow, float) if elbow is not None else (sh + w) / 2 + np.array([-0.08 * np.sign(-w[0] - 0.05), -0.08, 0])
    return p


def _keyframes(keys, length_s: float, contact_s=None) -> dict:
    """Clip from (time, pose) keyframes, Catmull-Rom interpolated at CLIP_FPS."""
    times = np.array([k[0] for k in keys])
    poses = np.array([k[1] for k in keys])
    n = int(round(length_s * CLIP_FPS)) + 1
    frames = []
    for i in range(n):
        t = times[0] + i / CLIP_FPS
        k = int(np.clip(np.searchsorted(times, t, side="right") - 1, 0, len(times) - 2))
        u = (t - times[k]) / (times[k + 1] - times[k])
        p0, p1, p2 = poses[max(k - 1, 0)], poses[k], poses[k + 1]
        p3 = poses[min(k + 2, len(poses) - 1)]
        u2, u3 = u * u, u * u * u
        frames.append(0.5 * ((2 * p1) + (-p0 + p2) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u2
                             + (-p0 + 3 * p1 - 3 * p2 + p3) * u3))
    take = {"frames": np.array(frames)}
    if contact_s is not None:
        take["contact"] = int(round((contact_s - times[0]) * CLIP_FPS))
    return take


def procedural_clips() -> dict:
    """The fallback motions (right-handed), same format as recorded ones."""
    idle = []
    for i in range(72):                       # 2.4 s loop: weight shifts side to side
        a = 2 * math.pi * i / 72
        p = REST.copy()
        p[:, 0] += 0.02 * math.sin(a) * (p[:, 1] > -0.2)    # upper body sways over the feet
        p[UPPER, 1] += 0.008 * math.sin(2 * a)               # breathing
        p[J["r_wr"], 0] += 0.02 * math.sin(a + 0.6)
        idle.append(p)
    side = []
    for i in range(18):                       # one step cycle in 0.6 s
        a = 2 * math.pi * i / 18
        p = REST.copy()
        p[J["l_an"], 0] += 0.10 * math.sin(a)
        p[J["r_an"], 0] += 0.10 * math.sin(a)
        p[J["l_an"], 1] += 0.06 * max(0.0, math.sin(a))      # lift the leading foot
        p[J["r_an"], 1] += 0.06 * max(0.0, -math.sin(a))
        p[J["l_kn"], 0] += 0.05 * math.sin(a)
        p[J["r_kn"], 0] += 0.05 * math.sin(a)
        side.append(p)
    # Strokes: the hand is fastest at contact (as in a real stroke, and as
    # tools/record_swing.py assumes): equal distances 0.25 s before and after.
    forehand = _keyframes([
        (-0.60, _stroke_key(0.00, (-0.22, 0.20, -0.38))),
        (-0.25, _stroke_key(0.55, (-0.62, 0.28, 0.06), (-0.42, 0.30, 0.00))),   # wind-up: back
        (0.00, _stroke_key(0.05, (-0.22, 0.22, -0.46), (-0.30, 0.26, -0.18))),  # contact, in front
        (0.25, _stroke_key(-0.55, (0.18, 0.48, -0.32), (-0.08, 0.36, -0.30), lift=0.02)),  # across
        (0.50, _stroke_key(-0.25, (0.02, 0.32, -0.38))),
    ], 1.10, 0.0)
    # Backhand: the hand goes back by the left hip, then sweeps forward and
    # across toward the right (-x), the mirror image of the forehand
    backhand = _keyframes([
        (-0.60, _stroke_key(0.00, (-0.22, 0.20, -0.38))),
        (-0.25, _stroke_key(-0.50, (0.50, 0.16, -0.06), (0.12, 0.30, -0.10))),   # wound up by the left hip
        (0.00, _stroke_key(-0.15, (0.24, 0.22, -0.46), (0.02, 0.30, -0.24))),    # contact, left side
        (0.25, _stroke_key(0.35, (-0.02, 0.46, -0.32), (0.02, 0.40, -0.26))),    # out to the right, up
        (0.50, _stroke_key(0.10, (-0.15, 0.30, -0.38))),
    ], 1.10, 0.0)
    return {"idle": {"takes": [{"frames": np.array(idle)}], "hand": "right", "source": "procedural"},
            "sidestep": {"takes": [{"frames": np.array(side)}], "hand": "right", "source": "procedural"},
            "forehand": {"takes": [forehand], "hand": "right", "source": "procedural"},
            "backhand": {"takes": [backhand], "hand": "right", "source": "procedural"}}


def load_clips(folder: Path = MOTION_DIR) -> dict:
    """Recorded clips where they exist (assets/motions/<name>.json), the
    procedural ones otherwise. A broken file is reported and replaced."""
    clips = procedural_clips()
    for name in MOTION_NAMES:
        path = folder / f"{name}.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            assert data["joints"] == JOINTS, "different joint list"
            takes = []
            for take in data["takes"]:
                frames = np.array(take["frames"], float)
                assert frames.ndim == 3 and frames.shape[1:] == (len(JOINTS), 3)
                t = {"frames": frames}
                if "contact" in take:
                    t["contact"] = int(take["contact"])
                takes.append(t)
            assert takes, "no takes"
            clips[name] = {"takes": takes, "hand": data.get("hand", "right"),
                           "source": f"recorded {data.get('recorded', '')}".strip()}
        except Exception as e:
            print(f"opponent: can't use {path.name} ({e}); using the procedural {name}")
    return clips


def sample(frames: np.ndarray, index: float) -> np.ndarray:
    """Pose at a fractional frame index (clamped)."""
    index = min(max(index, 0.0), len(frames) - 1.0)
    i = int(index)
    f = index - i
    return frames[i] if f == 0 or i + 1 >= len(frames) else frames[i] * (1 - f) + frames[i + 1] * f


def retarget(src: np.ndarray) -> np.ndarray:
    """Bone directions from the source pose, the figure's own bone lengths."""
    out = np.zeros_like(src)
    for a, b, length in BONE_IDX:
        d = src[b] - src[a]
        n = math.sqrt(float(d @ d))
        out[b] = out[a] + (d / n * length if n > 1e-6 else (REST[b] - REST[a]) / np.linalg.norm(REST[b] - REST[a]) * length)
    return out


# ------------------------------------------------------------ the figure
class OpponentFigure:
    def __init__(self, color: str, clips: dict = None, seed: int = None):
        self.color = color
        self.p = PERSONALITIES[color]
        self.clips = clips or load_clips()
        self.hand = self.clips["forehand"]["hand"]
        self.side = -1.0 if self.hand == "right" else 1.0   # paddle-hand side (x sign, seen from me)
        self.wr, self.el = J[self.hand[0] + "_wr"], J[self.hand[0] + "_el"]
        self.rng = random.Random(seed)
        self.root = np.array([0.0, HIP_HEIGHT, config.OPP_HIT_Z + 0.45])
        self.t = 0.0               # real time, for idle / reactions
        self.step_phase = 0.0
        self.step_w = 0.0
        self.shot = None           # current stroke (see _new_shot)
        self.reaction_t = None     # time since my miss (for the reaction), or None
        self._gt = None            # last game_time seen
        self.joints = None         # world positions of the joints (last update)
        self.paddle = None         # world position of the paddle center
        self.rest_paddle = self._paddle_local(retarget(self._idle(0.0)))

    # ------------------------------------------------------------ poses
    def _idle(self, t: float) -> np.ndarray:
        frames = self.clips["idle"]["takes"][0]["frames"]
        pose = sample(frames, (t * CLIP_FPS) % (len(frames) - 1))
        rest = frames[0]
        pose = rest + self.p["sway"] * (pose - rest)
        return pose - pose[J["hip"]]           # every pose has its hip center at 0

    def _stroke(self, shot, tc: float) -> np.ndarray:
        """The stroke's pose at tc seconds from contact, amplified by personality."""
        take = shot["take"]
        frames = take["frames"]
        pose = sample(frames, take["contact"] + tc * CLIP_FPS)
        start = frames[0]
        out = pose.copy()
        out[UPPER] = start[UPPER] + self.p["amp"] * (pose[UPPER] - start[UPPER])
        return out - out[J["hip"]]

    def _paddle_local(self, fig: np.ndarray) -> np.ndarray:
        d = fig[self.wr] - fig[self.el]
        return fig[self.wr] + d / (np.linalg.norm(d) + 1e-9) * PADDLE_REACH

    def _new_shot(self, target, serve: bool):
        """Forehand if the ball arrives on the paddle-hand side of the body."""
        forehand = serve or (target[0] - self.root[0]) * self.side > -0.08
        name = "forehand" if forehand else "backhand"
        take = self.rng.choice(self.clips[name]["takes"])
        shot = {"name": name, "take": take, "target": np.array(target, float), "serve": serve,
                "pre": take["contact"] / CLIP_FPS, "post": (len(take["frames"]) - 1 - take["contact"]) / CLIP_FPS,
                "tc": -9.0, "after": False}
        # Where the paddle is (relative to the hips) at the contact frame: the
        # body is placed so that this lands on the ball.
        shot["contact_paddle"] = self._paddle_local(retarget(self._stroke(shot, 0.0)))
        self.shot = shot

    # ------------------------------------------------------------ update
    def update(self, game, dt: float, events=()):
        """Once per frame, after GameState.update(). Reads the game only."""
        import game_state as gs
        if getattr(game, "paused", False):
            return
        gdt = 0.0 if self._gt is None else max(0.0, game.game_time - self._gt)
        self._gt = game.game_time
        self.t += dt
        b = game.ball

        # --- strokes: follow the ball
        contact_now = False
        if game.state == gs.READY:
            if self.shot is None or not self.shot["serve"]:
                self._new_shot((b.x, b.y, config.OPP_HIT_Z), serve=True)
            self.shot["tc"] = -game.ready_left            # GO = contact
        elif gs.EVENT_GO in events and self.shot is not None and self.shot["serve"]:
            self.shot["after"], self.shot["tc"], contact_now = True, 0.0, True
        elif gs.EVENT_OPP_HIT in events and self.shot is not None and not self.shot["after"]:
            self.shot["after"], self.shot["tc"], contact_now = True, 0.0, True
        elif (game.state == gs.PLAYING and not game.coming_to_me and b.vz > 0
              and (self.shot is None or self.shot.get("key") != game.opponent.react_at)):
            spot = gs.predict_crossing_xy(b, config.OPP_HIT_Z)
            if spot is not None:
                self._new_shot((spot[0], max(spot[1], 0.15), config.OPP_HIT_Z), serve=False)
                self.shot["key"] = game.opponent.react_at
        if self.shot is not None:
            if self.shot["after"] and not contact_now:   # (the contact frame shows tc = 0 exactly)
                self.shot["tc"] += gdt if not self.shot["serve"] else dt
                if self.shot["tc"] > self.shot["post"] + 0.3:
                    self.shot = None
            elif game.state == gs.PLAYING and not game.coming_to_me and b.vz > 0:
                self.shot["tc"] = -(config.OPP_HIT_Z - b.z) / b.vz   # time to contact, from the ball
            elif game.state not in (gs.READY, gs.PLAYING):
                self.shot = None

        # --- reactions to my miss
        if gs.EVENT_LIFE_OFFER in events or (gs.EVENT_GAME_OVER in events and self.reaction_t is None):
            self.reaction_t = 0.0
        if gs.EVENT_LIFE_USED in events or game.state in (gs.READY, gs.PLAYING):
            self.reaction_t = None
        elif self.reaction_t is not None:
            self.reaction_t += dt

        # --- where the body goes: its paddle follows the Opponent AI's paddle
        paddle_off = self.shot["contact_paddle"] if self.shot is not None else self.rest_paddle
        want_x = game.opponent.x - paddle_off[0]
        want_z = config.OPP_HIT_Z - paddle_off[2]
        k = 1 - math.exp(-dt / 0.10)
        old_x = self.root[0]
        self.root[0] += (want_x - self.root[0]) * k
        self.root[2] += (want_z - self.root[2]) * k
        speed = abs(self.root[0] - old_x) / dt if dt > 0 else 0.0
        self.step_phase = (self.step_phase + abs(self.root[0] - old_x) / 0.45) % 1.0
        self.step_w += (min(1.0, speed / 0.6) - self.step_w) * (1 - math.exp(-dt / 0.08))
        self._pose_now()

    def _pose_now(self):
        pose = self._idle(self.t)
        side = self.clips["sidestep"]["takes"][0]["frames"]
        if self.step_w > 0.01:
            pose[LEGS] += self.step_w * (sample(side, self.step_phase * (len(side) - 1))[LEGS] - pose[LEGS])
        root = self.root.copy()
        root[1] += self.p["bob"] * abs(math.sin(self.t * 2 * math.pi / 0.9))
        envelope = 0.0
        shot = self.shot
        if shot is not None:
            tc = shot["tc"]
            if tc <= 0:
                w = smoothstep((tc + shot["pre"] + 0.25) / 0.25)
                envelope = smoothstep((tc + 0.4) / 0.4)
            else:
                w = 1 - smoothstep((tc - shot["post"]) / 0.3)
                envelope = 1 - smoothstep(tc / 0.3)
            if w > 0:
                pose[UPPER] += w * (self._stroke(shot, tc)[UPPER] - pose[UPPER])
                root[1] = self.root[1]          # (no bob while swinging)
        if self.reaction_t is not None:
            pose, root = self._reaction(pose, root, self.reaction_t)
        fig = retarget(pose)
        paddle = self._paddle_local(fig)
        if envelope > 0:
            # Nudge the arm so the paddle center is exactly on the ball at contact
            corr = shot["target"] - (root + shot["contact_paddle"])
            fig[self.wr] += envelope * corr
            fig[self.el] += 0.5 * envelope * corr
            paddle = paddle + envelope * corr
        self.joints = fig + root
        self.paddle = paddle + root

    def _reaction(self, pose, root, t):
        if self.p["reaction"] == "celebrate":           # green: jump with both arms up
            length = 1.8
            w = smoothstep(t / 0.15) * (1 - smoothstep((t - length + 0.3) / 0.3))
            up = pose.copy()
            wig = 0.05 * math.sin(t * 14)
            up[J["l_el"]] = (0.32, 0.70, -0.02)
            up[J["r_el"]] = (-0.32, 0.70, -0.02)
            up[J["l_wr"]] = (0.30 + wig, 0.98, -0.04)
            up[J["r_wr"]] = (-0.30 + wig, 0.98, -0.04)
            pose = pose + w * (up - pose)
            root = root.copy()
            root[1] += w * 0.10 * abs(math.sin(t * math.pi / 0.35))
        else:                                            # blue: two slow nods
            length = 1.3
            w = smoothstep(t / 0.15) * (1 - smoothstep((t - length + 0.3) / 0.3))
            pitch = w * 0.45 * max(0.0, math.sin(t * 2 * math.pi / 0.65))
            neck = pose[J["neck"]]
            pose = pose.copy()
            pose[J["head"]] = neck + 0.2 * np.array([0, math.cos(pitch), -math.sin(pitch) - 0.2])
        return pose, root

    # ------------------------------------------------------------ preview pose
    def show_pose(self, pose: np.ndarray):
        """Put the figure in one raw clip pose (tools/record_swing.py --view)."""
        pose = pose - pose[J["hip"]]
        fig = retarget(pose)
        self.joints = fig + self.root
        self.paddle = self._paddle_local(fig) + self.root

    def preview_update(self, dt: float, react: bool = False):
        """Select screen: idle (or the reaction) without a game."""
        self.t += dt
        if react:
            self.reaction_t = 0.0 if self.reaction_t is None else self.reaction_t + dt
        self._pose_now()

    # ------------------------------------------------------------ drawing
    def draw(self, screen, view):
        """view: Projection (the game's camera, or a preview camera)."""
        if self.joints is None:
            self._pose_now()
        draw_figure(screen, view, self.joints, self.paddle, self.p, self.wr)


class Projection:
    """Pinhole camera like view3d.py (same formulas), so a preview can use
    its own camera. cam = (x, y, z) of the eye; it looks along +z."""

    def __init__(self, cam, focal, center):
        self.cam = np.array(cam, float)
        self.focal, self.cx, self.cy = focal, center[0], center[1]

    @staticmethod
    def game():
        return Projection((config.VIEW_CAM_X, config.VIEW_CAM_Y, config.VIEW_CAM_Z),
                          config.VIEW_FOCAL_PX, (config.WINDOW_WIDTH / 2, config.VIEW_HORIZON_Y))

    def points(self, pts: np.ndarray):
        d = np.maximum(pts[:, 2] - self.cam[2], 0.05)
        sx = self.cx + self.focal * (pts[:, 0] - self.cam[0]) / d
        sy = self.cy - self.focal * (pts[:, 1] - self.cam[1]) / d
        return np.stack([sx, sy], axis=1)


def _shade(color, normal) -> tuple:
    light = 0.55 + 0.45 * max(0.0, float(normal @ LIGHT))
    return tuple(int(c * light) for c in color)


def _box(a, b, width, depth, right):
    """8 corners of a box from point a to point b (the bone), `width` across
    (along `right`) and `depth` front-to-back."""
    up = b - a
    length = np.linalg.norm(up)
    up = up / length if length > 1e-6 else np.array([0, 1.0, 0])
    r = right - (right @ up) * up
    r = r / (np.linalg.norm(r) + 1e-9)
    f = np.cross(up, r)                       # toward me when upright and facing me
    hw, hd = width / 2, depth / 2
    corners = [a + sx * hw * r + sz * hd * f for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    corners += [c + up * length for c in corners]
    return np.array(corners), (r, up, f)


# faces as corner indexes, with the axis (0 r, 1 up, 2 f) and sign of their normal
FACES = [((0, 1, 2, 3), 1, -1), ((4, 5, 6, 7), 1, 1), ((0, 1, 5, 4), 2, -1),
         ((2, 3, 7, 6), 2, 1), ((1, 2, 6, 5), 0, 1), ((3, 0, 4, 7), 0, -1)]


def draw_figure(screen, view, joints, paddle, p, wrist_index):
    """Painter's algorithm: every visible box face, far to near."""
    j = lambda n: joints[J[n]]
    right = j("l_sh") - j("r_sh")
    hip_right = j("l_hip") - j("r_hip")
    body, legs = p["body"], p["legs"]
    parts = []   # (corners, axes, color, kind)

    def add(a, b, width, depth, color, kind="", r=right):
        corners, axes = _box(a, b, width, depth, r)
        parts.append((corners, axes, color, kind))

    for side in ("l", "r"):
        add(j(f"{side}_hip"), j(f"{side}_kn"), 0.17, 0.17, legs, r=hip_right)
        add(j(f"{side}_kn"), j(f"{side}_an"), 0.16, 0.16, legs, r=hip_right)
        foot = j(f"{side}_an")
        add(foot - (0, 0.06, 0.07), foot - (0, 0.0, 0.07) + (0, 0, -0.12), 0.15, 0.09, OUTLINE, r=hip_right)
        add(j(f"{side}_sh"), j(f"{side}_el"), 0.12, 0.12, body)
        add(j(f"{side}_el"), j(f"{side}_wr"), 0.11, 0.11, body)
        wr = j(f"{side}_wr")
        el = j(f"{side}_el")
        d = (wr - el) / (np.linalg.norm(wr - el) + 1e-9)
        add(wr - d * 0.02, wr + d * 0.09, 0.10, 0.08, SKIN)                  # mitten hand
    hip_c = j("hip")
    add(hip_c - (0, 0.05, 0), hip_c + (0, 0.07, 0), 0.40, 0.21, legs, r=hip_right)   # hips block
    add(hip_c + (0, 0.05, 0), j("neck"), 0.44, 0.22, body, "torso")
    neck = j("neck")
    up = j("head") - neck
    up = up / (np.linalg.norm(up) + 1e-9)
    add(neck + up * 0.03, neck + up * (0.03 + HEAD_SIZE), HEAD_SIZE, HEAD_SIZE, SKIN, "head")
    if p["hair"] == "flat":                      # blue: a flat dark cap
        top = neck + up * (0.03 + HEAD_SIZE)
        add(top - up * 0.02, top + up * 0.06, HEAD_SIZE * 1.06, HEAD_SIZE * 1.06,
            tuple(int(c * 0.5) for c in body))

    cam = view.cam
    faces = []
    for corners, axes, color, kind in parts:
        center = corners.mean(axis=0)
        for idx, axis, sign in FACES:
            normal = axes[axis] * sign
            fc = corners[list(idx)].mean(axis=0)
            if normal @ (cam - fc) <= 0:
                continue                         # facing away
            dist = float(np.linalg.norm(fc - cam))
            tag = kind if (kind and axis == 2 and sign == 1) else ""
            faces.append((dist, corners[list(idx)], _shade(color, normal), tag, corners))
    # The paddle: a disk at the paddle center, with a handle to the wrist
    faces.append((float(np.linalg.norm(paddle - cam)), None, None, "paddle", None))
    faces.sort(key=lambda f: -f[0])

    for dist, corners, color, tag, box in faces:
        if tag == "paddle":
            _draw_paddle(screen, view, paddle, joints[wrist_index], dist)
            continue
        pts = view.points(corners)
        pygame.draw.polygon(screen, color, pts)
        pygame.draw.polygon(screen, OUTLINE, pts, 1)
        if tag == "head":
            _draw_face(screen, pts, p)
            if p["hair"] == "spiky":
                _draw_spikes(screen, view, box, p)
        elif tag == "torso":
            # a plain stripe across the chest (no logos)
            a = pts[3] + (pts[0] - pts[3]) * 0.30
            b = pts[2] + (pts[1] - pts[2]) * 0.30
            pygame.draw.line(screen, (240, 240, 240), a, b, max(2, int(abs(pts[1][1] - pts[2][1]) * 0.08)))


def _lerp2(pts, u, v):
    """Point at (u, v) on a projected face (u across, v down from the top edge)."""
    top = pts[3] + (pts[2] - pts[3]) * u
    bottom = pts[0] + (pts[1] - pts[0]) * u
    return bottom + (top - bottom) * (1 - v)


def _draw_face(screen, pts, p):
    """Eyes and a mouth on the head's front face; green grins wider."""
    size = max(2, int(np.linalg.norm(pts[1] - pts[0]) * 0.08))
    for u in (0.32, 0.68):
        pygame.draw.circle(screen, OUTLINE, _lerp2(pts, u, 0.40), size)
    if p["reaction"] == "celebrate":
        mouth = [_lerp2(pts, u, 0.66 + 0.12 * math.sin(math.pi * (u - 0.25) / 0.5)) for u in np.linspace(0.25, 0.75, 7)]
    else:
        mouth = [_lerp2(pts, u, 0.70 + 0.05 * math.sin(math.pi * (u - 0.35) / 0.3)) for u in np.linspace(0.35, 0.65, 5)]
    pygame.draw.lines(screen, OUTLINE, False, mouth, max(2, size // 2))


def _draw_spikes(screen, view, head, p):
    """Green: three spikes along the middle of the head's top (corners 4-7)."""
    up = head[4] - head[0]
    up = up / (np.linalg.norm(up) + 1e-9)
    a0 = (head[4] + head[7]) / 2              # top face midline, from one side...
    a1 = (head[5] + head[6]) / 2              # ...to the other
    color = tuple(min(255, int(c * 1.15)) for c in p["body"])
    for u in (0.2, 0.5, 0.8):
        base0 = a0 + (a1 - a0) * (u - 0.17)
        base1 = a0 + (a1 - a0) * (u + 0.17)
        tip = a0 + (a1 - a0) * u + up * 0.14
        spike = view.points(np.array([base0, base1, tip]))
        pygame.draw.polygon(screen, color, spike)
        pygame.draw.polygon(screen, OUTLINE, spike, 1)


def _draw_paddle(screen, view, center, wrist, dist):
    c, w = view.points(np.array([center, wrist]))
    r = view.focal * config.PADDLE_RADIUS_M / max(center[2] - view.cam[2], 0.05)
    pygame.draw.line(screen, PADDLE_HANDLE, w, c, max(3, int(r * 0.3)))
    pygame.draw.circle(screen, PADDLE_RUBBER, c, r)
    pygame.draw.circle(screen, OUTLINE, c, r, 2)
