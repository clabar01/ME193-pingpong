"""Motion capture for the opponent figure: record YOUR moves with the webcam.

(Not to be confused with tools/record_swings.py, which records labeled IMU
data from the paddle sensor.)

MediaPipe Pose gives 3D "world" landmarks (meters, centered between the hips)
for every camera frame. This tool records four motions, one after the other:

    idle      stand in your ready stance, paddle in hand        (4 s)
    forehand  3-4 forehand swings, with a short pause between   (9 s)
    backhand  3-4 backhand swings, with a short pause between   (9 s)
    sidestep  side-step left and right a few times              (6 s)

and saves them to assets/motions/<motion>.json. The game (laptop/opponent.py)
loads them at start; any motion that's missing stays procedural.

How each recording becomes a clip:
  - 15 joints are kept (hips, knees, ankles, shoulders, elbows, wrists, the
    nose for the head), turned into table axes (x = to the right as seen by
    the camera, y = up, z = away from the camera: you face the camera like the
    figure faces the player), resampled to 30 frames per second and smoothed
    over 3 frames. Short tracking gaps are filled in.
  - forehand / backhand: each swing is found by the paddle hand's sideways
    speed in the stroke's direction (across the body; the backswing goes the
    other way and doesn't count): a peak above SWING_MIN_SPEED (and at least
    half the fastest one), peaks at least 0.8 s apart. The middle of the
    fastest part of each stroke is taken as the CONTACT frame (the
    figure's paddle meets the ball there); 0.6 s before it and 0.5 s after it
    are kept. Every swing becomes one "take"; the figure picks one at random.
  - idle / sidestep: 2.4 s from the middle of the recording, its end blended
    into its start so it loops without a jump.

Run from the repo root (stand back so your whole body is in view; 2-3 m):
    python tools/record_swing.py                 # all four, guided
    python tools/record_swing.py --only forehand backhand
    python tools/record_swing.py --hand left     # if you hold the paddle in your left hand
    python tools/record_swing.py --view          # watch the saved motions on the figure
    python tools/record_swing.py --clear         # delete the recordings (back to procedural)
Keys: SPACE starts each recording (after a 3 s countdown), S skips a motion,
ESC quits. In --view: 1-4 pick the motion, ESC quits.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "laptop"))
import config  # noqa: E402
import opponent  # noqa: E402

MOTIONS = {   # name: (seconds to record, what to do)
    "idle": (4.0, "Stand in your READY stance, paddle in hand. Small natural sway."),
    "forehand": (9.0, "Do 3-4 FOREHAND swings. Pause about 1 s between them."),
    "backhand": (9.0, "Do 3-4 BACKHAND swings. Pause about 1 s between them."),
    "sidestep": (6.0, "SIDE-STEP left and right a few times, like moving to a ball."),
}
FPS = opponent.CLIP_FPS
SWING_MIN_SPEED = 1.0    # m/s: the paddle hand must sweep at least this fast across the body
SWING_GAP_S = 0.8        # two swings' peaks at least this far apart
PRE_S, POST_S = 0.6, 0.5 # kept before / after the contact frame
LOOP_S = 2.4             # idle / sidestep loop length
BLEND_S = 0.4            # loop: the last BLEND_S fade into the first frames

# MediaPipe landmark numbers for each joint (a pair = the midpoint)
LANDMARKS = {"hip": (23, 24), "l_hip": 23, "r_hip": 24, "l_kn": 25, "r_kn": 26, "l_an": 27, "r_an": 28,
             "neck": (11, 12), "l_sh": 11, "r_sh": 12, "l_el": 13, "r_el": 14, "l_wr": 15, "r_wr": 16,
             "head": 0}


# ------------------------------------------------------------ processing (testable)
def to_joints(world) -> np.ndarray:
    """MediaPipe world landmarks (x right in the image, y DOWN, z toward the
    camera = smaller) -> (15, 3) in table axes, hip center at 0."""
    def point(i):
        if isinstance(i, tuple):
            return (point(i[0]) + point(i[1])) / 2
        lm = world[i]
        return np.array([lm.x, -lm.y, lm.z])
    pose = np.array([point(LANDMARKS[name]) for name in opponent.JOINTS])
    return pose - pose[opponent.J["hip"]]


def resample(times, poses, fps=FPS) -> np.ndarray:
    """Evenly spaced frames at fps (linear in between; gaps get filled in),
    then a 3-frame moving average against tracking jitter."""
    times = np.asarray(times, float)
    poses = np.asarray(poses, float)
    t = np.arange(times[0], times[-1] + 1e-9, 1 / fps)
    flat = poses.reshape(len(poses), -1)
    out = np.stack([np.interp(t, times, flat[:, k]) for k in range(flat.shape[1])], axis=1)
    if len(out) >= 3:
        padded = np.concatenate([out[:1], out, out[-1:]])
        out = (padded[:-2] + padded[1:-1] + padded[2:]) / 3
    return out.reshape(len(t), *poses.shape[1:])


def stroke_speed(frames: np.ndarray, hand: str, stroke: str, fps=FPS) -> np.ndarray:
    """How fast the paddle hand moves IN THE STROKE'S DIRECTION (m/s), 0 when
    it moves the other way. Facing the camera, a right-handed forehand sweeps
    the hand across the body toward +x, a backhand toward -x (left-handed:
    mirrored). Counting only that direction ignores the backswing, which is
    also fast but goes the other way."""
    w = frames[:, opponent.J[hand[0] + "_wr"], 0]
    sign = (1 if stroke == "forehand" else -1) * (1 if hand == "right" else -1)
    v = np.zeros(len(w))
    v[1:-1] = (w[2:] - w[:-2]) * fps / 2 * sign
    if len(v) > 2:
        v[0], v[-1] = v[1], v[-2]
    return np.maximum(v, 0.0)


def fastest_part(speed: np.ndarray, peak: int, fps=FPS) -> int:
    """The CONTACT frame: the middle of the fastest part of the stroke (frames
    within 0.25 s of the peak at >= 80% of its speed, weighted by how much
    faster). Steadier than the single fastest frame when tracking noise makes
    two nearly equal peaks."""
    lo, hi = max(0, peak - int(0.25 * fps)), min(len(speed), peak + int(0.25 * fps) + 1)
    w = np.maximum(speed[lo:hi] - 0.8 * speed[peak], 0.0)
    return int(round(float((np.arange(lo, hi) * w).sum() / w.sum()))) if w.sum() > 0 else peak


def find_swings(frames: np.ndarray, hand: str, stroke: str, fps=FPS) -> list:
    """Takes for the game: [{"frames": ..., "contact": index}], one per swing."""
    speed = stroke_speed(frames, hand, stroke, fps)
    if len(speed) == 0 or speed.max() < SWING_MIN_SPEED:
        return []
    threshold = max(SWING_MIN_SPEED, 0.5 * speed.max())
    pre, post = int(round(PRE_S * fps)), int(round(POST_S * fps))
    peaks = []
    for i in np.argsort(-speed):                      # fastest first
        if speed[i] < threshold:
            break
        if all(abs(i - p) >= SWING_GAP_S * fps for p in peaks):
            peaks.append(int(i))
    takes = []
    for p in sorted(peaks):
        p = fastest_part(speed, p, fps)
        if p - pre < 0 or p + post >= len(frames):
            continue                                   # cut off by the start / end of the recording
        takes.append({"frames": frames[p - pre:p + post + 1], "contact": pre,
                      "peak_speed": round(float(speed[p]), 2)})
    return takes


def make_loop(frames: np.ndarray, fps=FPS) -> np.ndarray:
    """LOOP_S from the middle, the end blended into the start (seamless)."""
    n, blend = int(round(LOOP_S * fps)), int(round(BLEND_S * fps))
    if len(frames) < n + blend:
        n = max(2, len(frames) - blend)
    start = (len(frames) - n - blend) // 2
    clip = frames[start:start + n + blend].copy()
    for k in range(blend):                             # crossfade the extra tail over the head
        a = (k + 1) / (blend + 1)
        clip[k] = (1 - a) * clip[n + k] + a * clip[k]
    return clip[:n]


def build_clip(name: str, times, poses, hand: str) -> dict:
    frames = resample(times, poses)
    if name in ("forehand", "backhand"):
        takes = find_swings(frames, hand, name)
    else:
        takes = [{"frames": make_loop(frames)}]
    return {"name": name, "joints": opponent.JOINTS, "fps": FPS, "hand": hand,
            "recorded": time.strftime("%Y-%m-%d %H:%M"),
            "takes": [{**t, "frames": np.round(t["frames"], 3).tolist()} for t in takes]}


def save(clip: dict, folder: Path = opponent.MOTION_DIR) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{clip['name']}.json"
    path.write_text(json.dumps(clip))
    return path


# ------------------------------------------------------------ recording (camera + pygame)
def record(names, hand):
    import cv2
    import mediapipe as mp
    import pygame
    from mediapipe.tasks.python import vision

    landmarker = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(REPO_ROOT / config.POSE_MODEL_PATH)),
        running_mode=vision.RunningMode.VIDEO, num_poses=1))
    cap = cv2.VideoCapture(config.CAMERA_INDEX)
    if not cap.isOpened():
        sys.exit(f"Can't open camera {config.CAMERA_INDEX} (camera permission? CAMERA_INDEX in config.py)")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
    pygame.init()
    screen = pygame.display.set_mode((960, 640))
    pygame.display.set_caption("Record your moves for the opponent")
    big, font, small = (pygame.font.SysFont(None, s) for s in (72, 36, 26))
    skeleton = [(c.start, c.end) for c in vision.PoseLandmarksConnections.POSE_LANDMARKS]
    t0 = time.monotonic()
    last_ts = -1

    def frame():
        """One camera frame: (pygame surface of the mirrored view, world landmarks or None)."""
        nonlocal last_ts
        ok, bgr = cap.read()
        if not ok:
            return None, None
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        ts = max(int((time.monotonic() - t0) * 1000), last_ts + 1)
        last_ts = ts
        result = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
        view = cv2.flip(rgb, 1)
        h, w = view.shape[:2]
        world = None
        if result.pose_landmarks:
            lm = result.pose_landmarks[0]
            for a, b in skeleton:
                cv2.line(view, (int((1 - lm[a].x) * w), int(lm[a].y * h)),
                         (int((1 - lm[b].x) * w), int(lm[b].y * h)), (0, 200, 255), 3)
            world = result.pose_world_landmarks[0]
        surf = pygame.image.frombuffer(view.tobytes(), (w, h), "RGB")
        return pygame.transform.smoothscale(surf, (640, int(640 * h / w))), world

    def show(view, lines, color=(235, 235, 235)):
        screen.fill((18, 24, 38))
        if view is not None:
            screen.blit(view, (160, 120))
        for i, (text, f) in enumerate(lines):
            surf = f.render(text, True, color if i == 0 else (180, 190, 210))
            screen.blit(surf, surf.get_rect(center=(480, 30 + i * 38)))
        pygame.display.flip()

    try:
        for name in names:
            seconds, what = MOTIONS[name]
            # Wait for SPACE (S = skip)
            go = None
            while go is None:
                for e in pygame.event.get():
                    if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                        return
                    if e.type == pygame.KEYDOWN and e.key == pygame.K_SPACE:
                        go = True
                    if e.type == pygame.KEYDOWN and e.key == pygame.K_s:
                        go = False
                view, world = frame()
                show(view, [(f"{name.upper()}", big), (what, font),
                            ("whole body in view?  SPACE = record, S = skip, ESC = quit"
                             + ("" if world is not None else "   (no body seen)"), small)])
            if not go:
                continue
            start = time.monotonic() + 3.0
            times, poses = [], []
            while time.monotonic() < start + seconds:
                pygame.event.pump()
                view, world = frame()
                now = time.monotonic()
                if now < start:
                    show(view, [(f"{name.upper()} in {int(start - now) + 1}", big), (what, font)])
                    continue
                if world is not None:
                    times.append(now - start)
                    poses.append(to_joints(world))
                show(view, [(f"RECORDING {name.upper()}  {start + seconds - now:4.1f} s", big),
                            (what, font), (f"{len(poses)} poses", small)], (255, 95, 95))
            if len(poses) < 10:
                print(f"{name}: only {len(poses)} poses tracked; not saved (is your whole body in view?)")
                continue
            clip = build_clip(name, times, poses, hand)
            if not clip["takes"]:
                print(f"{name}: no swing found (the paddle hand never swept across faster than "
                      f"{SWING_MIN_SPEED} m/s); not saved. Try bigger, faster swings, facing the camera.")
                continue
            path = save(clip)
            extra = (f", {len(clip['takes'])} swings (peak hand speed "
                     + ", ".join(f"{t['peak_speed']:.1f}" for t in clip["takes"]) + " m/s)"
                     if name in ("forehand", "backhand") else "")
            print(f"{name}: {len(poses)} poses in {seconds:.0f} s -> {path.relative_to(REPO_ROOT)}{extra}")
    finally:
        cap.release()
        pygame.quit()


def view(names):
    """Play the saved (or procedural) motions on the figure."""
    import pygame
    pygame.init()
    screen = pygame.display.set_mode((900, 640))
    pygame.display.set_caption("Opponent motions (1-4 pick, ESC quits)")
    font = pygame.font.SysFont(None, 32)
    clips = opponent.load_clips()
    figs = [opponent.OpponentFigure(c, clips) for c in ("blue", "green")]
    current = 0
    clock = pygame.time.Clock()
    t = 0.0
    while True:
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                return
            if e.type == pygame.KEYDOWN and pygame.K_1 <= e.key <= pygame.K_4:
                current, t = e.key - pygame.K_1, 0.0
        t += clock.tick(60) / 1000
        name = opponent.MOTION_NAMES[current]
        clip = clips[name]
        takes = clip["takes"]
        length = sum(len(k["frames"]) for k in takes)
        i = int(t * FPS) % length
        for take in takes:                 # play every take one after another
            if i < len(take["frames"]):
                break
            i -= len(take["frames"])
        screen.fill((18, 24, 38))
        for fig, x in zip(figs, (260, 640)):
            fig.show_pose(take["frames"][i])
            root = fig.root
            proj = opponent.Projection((root[0], root[1] + 0.08, root[2] - 4.0), 380 * 4.0 / 1.8, (x, 360))
            fig.draw(screen, proj)
        contact = "  CONTACT" if take.get("contact") == i else ""
        label = f"{current + 1}: {name} ({clip['source']}) frame {i}{contact}   [1-4 pick, ESC quits]"
        screen.blit(font.render(label, True, (235, 235, 235)), (20, 20))
        pygame.display.flip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", choices=list(MOTIONS), help="record only these")
    ap.add_argument("--hand", choices=["right", "left"], default="right", help="your paddle hand")
    ap.add_argument("--view", action="store_true", help="watch the saved motions on the figure")
    ap.add_argument("--clear", action="store_true", help="delete the recordings")
    a = ap.parse_args()
    if a.clear:
        for name in MOTIONS:
            path = opponent.MOTION_DIR / f"{name}.json"
            if path.exists():
                path.unlink()
                print("deleted", path.relative_to(REPO_ROOT))
        return
    if a.view:
        view(a.only or list(MOTIONS))
        return
    record(a.only or list(MOTIONS), a.hand)


if __name__ == "__main__":
    main()
