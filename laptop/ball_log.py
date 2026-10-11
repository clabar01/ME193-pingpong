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
COLUMNS = ["time", "level", "mode", "result", "reason", "distance_cm", "off_by_cm",
           "tolerance_cm", "swing_offset_ms", "swing_peak_g", "swing_peak_dps", "hand_tracked"]
REASON_TEXT = {
    "OUT_OF_ZONE": "out of zone (paddle not on the ball)",
    "NO_SWING": "no swing",
    "SWING_TOO_EARLY": "swing too early",
    "SWING_TOO_LATE": "swing too late",
    "NO_CONTACT": "ball never reached the paddle",
}


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
        off = "-" if record["off_by_cm"] is None else f"{record['off_by_cm']:+.1f} cm"
        swing = "-" if record["swing_offset_ms"] is None else f"{record['swing_offset_ms']:+d} ms"
        print(f"ball {len(self.rows)}: {record['result'].upper()}{detail}  level {level}, "
              f"paddle off by {off}, swing {swing}")

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
        no_hand = sum(1 for r in self.rows if r["hand_tracked"] is False)
        if no_hand:
            lines.append(f"Wrist not tracked at contact on {no_hand} of {n} balls.")
        lines.append(f"Log: {self.path.relative_to(REPO_ROOT)}")
        return "\n".join(lines)

    def close(self):
        if self._file is not None:
            self._file.close()
