"""Per-ball log for diagnosing hard play: one CSV row per ball that reached me,
and a summary printed when the game quits.

Rows come from GameState.ball_results (see game_state._record). The file is
data/balls_<date-time>.csv (git-ignored), created at the first ball.
"""
import csv
import time
from collections import Counter
from pathlib import Path

import config

REPO_ROOT = Path(__file__).resolve().parent.parent
# dx/dy = paddle center - ball (cm; + = paddle right / higher), off_x/off_y = how far
# outside the hit zone (0 = inside; off_y empty = height not checked), tol = the
# level's tolerances, tracking = how the hand was found at contact.
# volley = hits so far in the rally when the ball came, speed_x = the ramp's speed factor then.
COLUMNS = ["time", "level", "mode", "volley", "speed_x", "result", "reason", "dx_cm", "dy_cm", "off_x_cm", "off_y_cm",
           "tol_x_cm", "tol_y_cm", "swing_offset_ms", "swing_window_ms", "swing_peak_g",
           "swing_peak_dps", "tracking", "hand_tracked", "rejected_why"]
REASON_TEXT = {
    "OUT_OF_ZONE": "out of zone (paddle not on the ball)",
    "NO_SWING": "no swing",
    "SWING_TOO_EARLY": "swing too early",
    "SWING_TOO_LATE": "swing too late",
    "SWING_REJECTED": "swing rejected by the IMU checks",
    "NO_CONTACT": "ball never reached the paddle",
}


def position_text(r: dict) -> str:
    """e.g. "12 cm too far left, 3 cm too low" / "on the ball" / "-"."""
    if r.get("dx_cm") is None:
        return "-"
    parts = []
    if (r["off_x_cm"] or 0) > 0:
        parts.append(f"{r['off_x_cm']:.0f} cm too far {'right' if r['dx_cm'] > 0 else 'left'}")
    if (r["off_y_cm"] or 0) > 0:
        parts.append(f"{r['off_y_cm']:.0f} cm too {'high' if r['dy_cm'] > 0 else 'low'}")
    return ", ".join(parts) if parts else "on the ball"


class BallLog:
    def __init__(self, mode: str):
        self.mode = mode
        self.path = REPO_ROOT / "data" / f"balls_{time.strftime('%Y%m%d-%H%M%S')}.csv"
        self.rows = []
        self._file = None
        self._writer = None

    def add(self, record: dict):
        if self._file is None:
            self.path.parent.mkdir(exist_ok=True)
            self._file = open(self.path, "w", newline="")
            self._writer = csv.DictWriter(self._file, fieldnames=COLUMNS)
            self._writer.writeheader()
        row = {"time": time.strftime("%H:%M:%S"), "mode": self.mode,
               **{k: ("" if v is None else v) for k, v in record.items()}}
        self.rows.append(row)
        self._writer.writerow(row)
        self._file.flush()
        level = config.LEVELS.get(record["level"], {}).get("name", record["level"])
        detail = f" ({REASON_TEXT.get(record['reason'], record['reason'])})" if record["reason"] else ""
        swing = "-" if record["swing_offset_ms"] is None else f"{record['swing_offset_ms']:+d} ms"
        print(f"ball {len(self.rows)}: {record['result'].upper()}{detail}  level {level}, "
              f"paddle {position_text(record)}, swing {swing}, tracking {record.get('tracking') or '-'}")

    def summary(self) -> str:
        n = len(self.rows)
        if n == 0:
            return "No balls reached the paddle this session."
        hits = sum(r["result"] == "hit" for r in self.rows)
        misses = [r["reason"] for r in self.rows if r["result"] == "miss"]
        lines = [f"Session summary: {n} balls, {hits} hits ({hits / n * 100:.0f}%), "
                 f"{len(misses)} misses ({len(misses) / n * 100:.0f}%)"]
        if misses:
            lines.append("Misses by reason:")
            for reason, count in Counter(misses).most_common():
                lines.append(f"  {REASON_TEXT.get(reason, reason):40s} {count:3d}  "
                             f"({count / len(misses) * 100:.0f}% of misses)")
        zone = [r for r in self.rows if r["reason"] == "OUT_OF_ZONE"]
        if zone:
            side = sum(1 for r in zone if (r["off_x_cm"] or 0) > 0)
            height = sum(1 for r in zone if (r["off_y_cm"] or 0) > 0)
            lines.append(f"Out of zone: {side} off left/right, {height} off in height "
                         f"(a ball can be both).")
        tracking = Counter(r["tracking"] for r in self.rows if r["tracking"])
        if tracking and set(tracking) != {"keys"}:
            lines.append("Hand tracking at contact: " + ", ".join(
                f"{k} {v}" for k, v in tracking.most_common()))
        lines.append(f"Log: {self.path.relative_to(REPO_ROOT)}")
        return "\n".join(lines)

    def close(self):
        if self._file is not None:
            self._file.close()


class EventLog:
    """data/events_<date-time>.log (git-ignored): every game event and every
    sound with its reason, one timestamped line each, so a session can be
    traced afterwards (e.g. which sound played on which event)."""

    def __init__(self):
        self.path = REPO_ROOT / "data" / f"events_{time.strftime('%Y%m%d-%H%M%S')}.log"
        self._file = None

    def write(self, line: str):
        if self._file is None:
            self.path.parent.mkdir(exist_ok=True)
            self._file = open(self.path, "w")
        now = time.time()
        self._file.write(f"{time.strftime('%H:%M:%S', time.localtime(now))}.{int(now % 1 * 1000):03d} {line}\n")
        self._file.flush()

    def close(self):
        if self._file is not None:
            self._file.close()
