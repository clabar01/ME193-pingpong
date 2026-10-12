"""Test the voice commands' accuracy, with or without background noise.

Run from the repo root.

LIVE (the real microphone, e.g. in a noisy classroom):
    python tools/voice_test.py --live
        prints everything it hears: accepted commands and ignored phrases (and why)
    python tools/voice_test.py --live --prompt 20
        asks you to say 20 random commands, one every few seconds, then reports
        how many were recognized correctly, missed, or recognized as the wrong
        command; then listens for --quiet-s seconds (default 30) while you DON'T
        give commands (let the room talk) and counts false triggers.

FILES (repeatable, no microphone):
    python tools/voice_test.py --files DIR [--snr 10 5 0]
        DIR has 16 kHz mono WAVs: cmd_<anything>_<command with _>.wav (should
        be recognized as that command) and babble_*.wav (talking that must NOT
        trigger anything). With --snr, every command is also mixed with
        babble + white noise at those signal-to-noise ratios in dB (10 dB =
        speech 3x louder than the noise; 0 dB = as loud as the noise).
"""
import argparse
import glob
import random
import sys
import time
import wave
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "laptop"))
import config  # noqa: E402
import voice  # noqa: E402

RATE = config.VOICE_SAMPLE_RATE


def load_model():
    from vosk import Model, SetLogLevel
    SetLogLevel(-1)
    return Model(str(REPO_ROOT / config.VOICE_MODEL_PATH))


def read(path) -> np.ndarray:
    with wave.open(path) as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1, f"{path}: need {RATE} Hz mono"
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)


def recognize(model, audio: np.ndarray) -> list:
    """Run a fresh matcher over the audio (+ 1 s of silence); accepted commands."""
    m = voice.CommandMatcher(model)
    x = np.concatenate([np.zeros(RATE // 2), audio, np.zeros(RATE)])
    out = []
    pcm = np.clip(x, -32768, 32767).astype(np.int16).tobytes()
    step = RATE // 10 * 2
    for i in range(0, len(pcm), step):
        for cmd, said, free, ok, why in m.feed(pcm[i:i + step], i / 2 / RATE):
            if ok:
                out.append(cmd)
    return out


def mix(speech: np.ndarray, noise: np.ndarray, snr_db: float, rng) -> np.ndarray:
    """Speech + noise scaled so speech power / noise power = snr_db."""
    if len(noise) < len(speech) + 2 * RATE:
        noise = np.tile(noise, int((len(speech) + 2 * RATE) / len(noise)) + 1)
    start = rng.integers(0, len(noise) - len(speech) - RATE)
    n = noise[start:start + len(speech) + RATE].copy()
    s = np.concatenate([np.zeros(RATE // 2), speech, np.zeros(RATE // 2)])[:len(n)]
    p_s = np.mean(speech ** 2)
    n *= np.sqrt(p_s / (np.mean(n ** 2) * 10 ** (snr_db / 10)))
    return s + n


def files_mode(folder, snrs):
    model = load_model()
    cmds = sorted(glob.glob(f"{folder}/cmd_*.wav"))
    babble = sorted(glob.glob(f"{folder}/babble_*.wav"))
    rng = np.random.default_rng(193)
    noise = np.concatenate([read(b) for b in babble]) if babble else None
    if noise is not None:   # several people at once: overlap the chatter, plus some hiss
        noise = noise + np.roll(noise, len(noise) // 3) + np.roll(noise, 2 * len(noise) // 3)
        noise = noise + rng.normal(0, np.std(noise) * 0.3, len(noise))
    for snr in [None] + list(snrs):
        right = wrong = missed = 0
        for path in cmds:
            want = Path(path).stem.split("_", 2)[2].replace("_", " ")
            audio = read(path) if snr is None else mix(read(path), noise, snr, rng)
            got = recognize(model, audio)
            if got == [want]:
                right += 1
            elif got:
                wrong += 1
                print(f"   {'clean' if snr is None else f'{snr:+.0f} dB'}: {Path(path).stem} -> {got}")
            else:
                missed += 1
                if snr is None:
                    print(f"   clean: {Path(path).stem} -> missed")
        label = "clean" if snr is None else f"SNR {snr:+.0f} dB"
        print(f"{label:12s} {len(cmds)} commands: {right} right ({right / len(cmds):.0%}), "
              f"{missed} missed, {wrong} wrong command")
    false = sum(len(recognize(model, read(b))) for b in babble)
    print(f"{len(babble)} chatter clips (no commands): {false} false triggers")


def live_mode(prompt: int, quiet_s: float):
    lis = voice.VoiceListener(enabled=True)
    while lis.status == "loading":
        time.sleep(0.1)
    if lis.status != "listening":
        print(f"voice: {lis.status}")
        return
    print("Listening (Ctrl+C to stop).")

    def drain(seconds):
        heard, end = [], time.monotonic() + seconds
        while time.monotonic() < end:
            for t, line in lis.poll_log():
                print("   " + line)
            heard += [c for c, _, _ in lis.poll()]
            time.sleep(0.05)
        return heard

    try:
        if not prompt:
            while True:
                drain(1.0)
        right = missed = wrong = 0
        for i in range(prompt):
            want = random.choice(config.VOICE_COMMANDS)
            print(f"[{i + 1}/{prompt}] say: {want.upper()}")
            got = drain(3.5)
            if want in got and len(set(got)) == 1:
                right += 1
            elif got:
                wrong += 1
                print(f"   -> heard {got}")
            else:
                missed += 1
                print("   -> (nothing)")
        if prompt:
            print(f"\n{prompt} prompts: {right} right ({right / prompt:.0%}), {missed} missed, {wrong} wrong")
        print(f"\nNow DON'T give commands for {quiet_s:.0f} s (let the room talk)...")
        false = drain(quiet_s)
        print(f"False triggers in {quiet_s:.0f} s of background: {len(false)} {false}")
    except KeyboardInterrupt:
        pass
    finally:
        lis.stop()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--prompt", type=int, default=0)
    ap.add_argument("--quiet-s", type=float, default=30)
    ap.add_argument("--files")
    ap.add_argument("--snr", type=float, nargs="*", default=[])
    a = ap.parse_args()
    if a.files:
        files_mode(a.files, a.snr)
    elif a.live:
        live_mode(a.prompt, a.quiet_s)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
