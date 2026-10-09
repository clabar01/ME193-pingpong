"""Which signal tells real swings from fake moves better: acceleration or rotation?

Run from the repo root after recording with tools/record_swings.py:

    python tools/analyze_swings.py                 # all data/imu_labeled_*.csv
    python tools/analyze_swings.py data/one.csv    # specific files

For every labeled trial it takes two numbers:
    peak accel magnitude = max over the trial of |a| - 1 g    (g)
    peak gyro magnitude  = max over the trial of |w|          (deg/s)
Then, for each of the two, it finds the single threshold that best splits
swings (above) from fakes (below) and reports how cleanly it does:
    balanced accuracy = average of (swings caught) and (fakes rejected)
    gap               = smallest swing peak - largest fake peak, as a fraction
                        of the distance between the two medians (> 0 means a
                        threshold exists that gets every trial right; bigger =
                        more room for error)
The better signal is the one with higher balanced accuracy (then bigger gap).
The suggested threshold is the middle of the gap (or the best split if the
two groups overlap). Also saves a figure to data/swing_analysis.png.
"""
import csv
import glob
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
FEATURES = {"accel": ("accel_mag", "g", "Peak acceleration, |a| - 1 g (g)"),
            "gyro": ("gyro_mag", "dps", "Peak rotation speed, |w| (deg/s)")}
SWING_COLOR, FAKE_COLOR = "#2a78d6", "#eb6834"   # categorical slots 1, 2 (light)
SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#6b6a63", "#e4e3dc"


def load(paths):
    """{(file, trial): (label, {feature: peak})}"""
    trials = {}
    for path in paths:
        peaks = defaultdict(lambda: {"label": None, "accel": float("-inf"), "gyro": float("-inf")})
        with open(path) as f:
            for row in csv.DictReader(f):
                p = peaks[row["trial"]]
                p["label"] = row["label"]
                p["accel"] = max(p["accel"], float(row["accel_mag"]))
                p["gyro"] = max(p["gyro"], float(row["gyro_mag"]))
        for trial, p in peaks.items():
            trials[(Path(path).name, trial)] = p
    return trials


def best_split(swings, fakes):
    """Threshold (swing if value > threshold) with the best balanced accuracy.
    Among equally good thresholds, the middle of the widest interval."""
    values = sorted(set(swings + fakes))
    candidates = [values[0] - 1e-9] + [(a + b) / 2 for a, b in zip(values, values[1:])]
    def score(th):
        caught = sum(v > th for v in swings) / len(swings)
        rejected = sum(v <= th for v in fakes) / len(fakes)
        return (caught + rejected) / 2
    best = max(score(c) for c in candidates)
    if min(swings) > max(fakes):
        return (min(swings) + max(fakes)) / 2, best      # clean gap: use its middle
    tied = [c for c in candidates if score(c) == best]
    return statistics.median(tied), best


def analyze(trials):
    swings = [p for p in trials.values() if p["label"] == "swing"]
    fakes = [p for p in trials.values() if p["label"] == "fake"]
    results = {}
    for feat, (_, unit, _) in FEATURES.items():
        s = [p[feat] for p in swings]
        f = [p[feat] for p in fakes]
        threshold, bal_acc = best_split(s, f)
        spread = statistics.median(s) - statistics.median(f)
        gap = (min(s) - max(f)) / spread if spread > 0 else float("-inf")
        results[feat] = {
            "swings": s, "fakes": f, "unit": unit, "threshold": threshold,
            "balanced_accuracy": bal_acc, "gap": gap,
            "missed_swings": sum(v <= threshold for v in s),
            "false_fakes": sum(v > threshold for v in f),
        }
    return results


def report(results, n_swings, n_fakes):
    print(f"\n{n_swings} swings, {n_fakes} fakes\n")
    print(f"{'':10s}{'swings: min / median / max':>32s}{'fakes: min / median / max':>32s}"
          f"{'threshold':>12s}{'bal. acc':>10s}{'gap':>8s}{'missed':>8s}{'false':>7s}")
    for feat, r in results.items():
        s, f, u = r["swings"], r["fakes"], r["unit"]
        fmt = (lambda v: f"{v:6.2f}") if u == "g" else (lambda v: f"{v:6.0f}")
        print(f"{feat + ' (' + u + ')':10s}"
              f"{fmt(min(s)) + ' /' + fmt(statistics.median(s)) + ' /' + fmt(max(s)):>32s}"
              f"{fmt(min(f)) + ' /' + fmt(statistics.median(f)) + ' /' + fmt(max(f)):>32s}"
              f"{fmt(r['threshold']):>12s}{r['balanced_accuracy'] * 100:9.1f}%{r['gap']:8.2f}"
              f"{r['missed_swings']:8d}{r['false_fakes']:7d}")
    winner = max(results, key=lambda k: (results[k]["balanced_accuracy"], results[k]["gap"]))
    r = results[winner]
    print(f"\nBetter signal: {winner.upper()} "
          f"(balanced accuracy {r['balanced_accuracy'] * 100:.1f}%, gap {r['gap']:.2f})")
    print(f"Suggested: FEATURE = \"{winner}\", SWING_THRESHOLD = {r['threshold']:.3g}  ({r['unit']})")
    return winner


def plot(results, winner, out: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 3.6), facecolor=SURFACE)
    for ax, (feat, r) in zip(axes, results.items()):
        ax.set_facecolor(SURFACE)
        for y, values, color, name in ((1, r["swings"], SWING_COLOR, "swing"),
                                       (0, r["fakes"], FAKE_COLOR, "fake")):
            jitter = [y + ((i * 37) % 11 - 5) * 0.025 for i in range(len(values))]
            ax.scatter(values, jitter, s=46, color=color, edgecolors=SURFACE, linewidths=1.5,
                       label=f"{name} ({len(values)})", zorder=3)
        ax.axvline(r["threshold"], color=INK, linestyle=(0, (4, 3)), linewidth=1.5, zorder=2)
        ax.text(r["threshold"], 1.45, f" threshold {r['threshold']:.3g} {r['unit']}",
                color=INK, fontsize=9, va="center")
        title = f"{FEATURES[feat][2]}"
        if feat == winner:
            title += "  (chosen)"
        ax.set_title(title, color=INK, fontsize=11, loc="left")
        ax.set_yticks([0, 1], ["fake", "swing"], color=INK)
        ax.set_ylim(-0.5, 1.7)
        ax.tick_params(colors=MUTED, length=0)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.text(0.99, 0.02, f"balanced accuracy {r['balanced_accuracy'] * 100:.0f}%",
                transform=ax.transAxes, ha="right", color=MUTED, fontsize=9)
    axes[0].legend(frameon=False, loc="upper left", fontsize=9, labelcolor=INK,
                   bbox_to_anchor=(0, -0.12), ncol=2)
    fig.tight_layout()
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    print(f"Figure: {out.relative_to(REPO_ROOT)}")


def main():
    paths = sys.argv[1:] or sorted(glob.glob(str(DATA_DIR / "imu_labeled_*.csv")))
    if not paths:
        sys.exit("No data. Record some first: python tools/record_swings.py")
    trials = load(paths)
    n_s = sum(p["label"] == "swing" for p in trials.values())
    n_f = sum(p["label"] == "fake" for p in trials.values())
    if n_s < 2 or n_f < 2:
        sys.exit(f"Need at least 2 swings and 2 fakes (have {n_s} and {n_f}).")
    if n_s < 10 or n_f < 10:
        print(f"Note: only {n_s} swings / {n_f} fakes; aim for 15+ of each for a reliable threshold.")
    print(f"Files: {', '.join(Path(p).name for p in paths)}")
    results = analyze(trials)
    winner = report(results, n_s, n_f)
    plot(results, winner, DATA_DIR / "swing_analysis.png")


if __name__ == "__main__":
    main()
