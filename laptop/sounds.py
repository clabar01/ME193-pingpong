"""Game sounds through pyaudio, on their own thread (the game never waits).

    hit      short "tock" on every hit
    tick     shorter, lighter, higher "tick" when the ball bounces on the table
    record   short bell chime: the volley count just passed the record (mid-rally,
             with the HIGH SCORE banner)
    death    "sad trombone" wah-wah-wah-waaah: the rally ended (I missed)
    slowmo   a falling "whoooop": SLOW-MO card used
    life     a quick rising four-note jingle: EXTRA LIFE card used
    count    short beep: each number of the serve countdown (3, 2, 1) and of the
             extra-life countdown
    go       higher, longer beep: GO, the ball is served

WHICH SOUND WHEN (SoundRules below; every sound is printed as "SOUND <name>:
<reason>", shown on screen and written to data/events_<time>.log):
  - ball bounces on the table (either side)          -> tick
  - my paddle hits the ball                           -> hit (tock)
      ...and that hit first takes the volley count past the record of when
      the rally started, and the count is >= MIN_RECORD_FOR_HIGH_SCORE
                                                      -> also record (chime) + the HIGH
                                                         SCORE banner, once per rally
  - before every serve: 3, 2, 1                        -> count on each number
      ...GO (the ball is served)                      -> go
  - SLOW-MO card used                                 -> slowmo
  - I miss with the extra life unused                 -> count (5), then count each
                                                         second (4 ... 1); no death yet
      ...EXTRA LIFE card shown in time                -> life (the rally goes on)
      ...the countdown runs out                       -> death
  - I miss otherwise (the rally ends)                 -> death
  - anything else (game start and new serve apart from their countdown, the opponent's returns, level
    change, calibration, record reset, the record loading from MQTT, a card
    that is used up or not usable now)                -> nothing
The opponent never misses, so a rally runs from the serve until I miss and
its volley count is my streak.

Adapted from the Whistling World Cup project (Lego/world_cup/songs.py): the
same note table, harmonics + attack/release envelope, and the same death
melody, synthesized with numpy.

Each sound is loaded from assets/sounds/<name>.wav if that file exists, else
synthesized. To use your own sound, replace the .wav file (any sample rate,
mono or stereo, 8/16/32-bit PCM). Regenerate the default files with:
    python laptop/sounds.py --write     (add --force to overwrite)
    python laptop/sounds.py             (play them all to check them)

One background thread owns the speaker: it keeps one output stream open and
mixes whatever is playing in short chunks (SOUND_CHUNK samples), so a hit can
play over a chime, and play() returns at once. The end-of-streak sounds
(death) stops everything else and plays alone. config.SOUND_MUTED = True
turns all of it off (no audio device is even opened).
"""
import queue
import sys
import threading
import wave
from pathlib import Path

import numpy as np

import config

REPO_ROOT = Path(__file__).resolve().parent.parent
SOUND_DIR = REPO_ROOT / "assets" / "sounds"
NAMES = ("hit", "tick", "record", "death", "slowmo", "life", "count", "go")
END_OF_STREAK = ("death",)   # stops everything else and plays alone
# Peak level of each built-in sound (the tick is meant to sit under the tock)
PEAK = {"tick": 0.4, "record": 0.8, "count": 0.6, "go": 0.75, "slowmo": 0.7, "life": 0.8}
SOUND_CHUNK = 512      # samples per write: ~12 ms at 44.1 kHz (latency of a new sound)

_NOTE_INDEX = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6,
               "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}


def note_hz(name: str) -> float:
    """'A4' -> 440.0, 'C#5' -> 554.4. 'R' = rest (0 Hz)."""
    if name == "R":
        return 0.0
    pitch, octave = name[:-1], int(name[-1])
    semis = _NOTE_INDEX[pitch] + 12 * (octave + 1) - 69     # MIDI number - A4
    return 440.0 * 2 ** (semis / 12)


# (note, seconds), as in the World Cup project. The last death note wobbles
# (vibrato) like a trombone.
SONGS = {
    "death":   [("G4", .38), ("F#4", .38), ("F4", .38), ("E4", 1.3)],
    "life":    [("C6", .06), ("E6", .06), ("G6", .06), ("C7", .22)],
    "count":   [("A5", .09)],
    "go":      [("A6", .2)],      # an octave above the count beep, and longer
}


def synthesize(name: str, rate: int) -> np.ndarray:
    """A sound as float samples in -1..1 (peak 0.9, or PEAK[name])."""
    if name == "tick":
        # Ball on the table: higher and much shorter than the paddle tock,
        # mostly click, and quieter (PEAK).
        n = int(rate * 0.035)
        t = np.arange(n) / rate
        rng = np.random.default_rng(194)
        wave_ = (np.sin(2 * np.pi * 3200 * t) * np.exp(-t / 0.004)
                 + 0.6 * np.sin(2 * np.pi * 5100 * t) * np.exp(-t / 0.002)
                 + 0.5 * rng.uniform(-1, 1, n) * np.exp(-t / 0.001))
    elif name == "slowmo":
        # A tone gliding down from 900 to 220 Hz over 0.55 s: time slowing down.
        n = int(rate * 0.55)
        t = np.arange(n) / rate
        hz = 900 * (220 / 900) ** (t / t[-1])                  # exponential glide
        phase = 2 * np.pi * np.cumsum(hz) / rate
        env = np.minimum(1, t / 0.02) * np.minimum(1, (t[-1] - t) / 0.12)
        wave_ = (np.sin(phase) + 0.3 * np.sin(2 * phase)) * env
    elif name == "record":
        # A bell, two quick strikes (E6 then B6): a different timbre from the
        # melodies, so it can't be mistaken for the fanfare.
        parts = []
        for hz, dur in ((note_hz("E6"), 0.12), (note_hz("B6"), 0.38)):
            t = np.arange(int(rate * dur)) / rate
            bell = (np.sin(2 * np.pi * hz * t)
                    + 0.5 * np.sin(2 * np.pi * 2.76 * hz * t) * np.exp(-t / 0.05)
                    + 0.25 * np.sin(2 * np.pi * 5.4 * hz * t) * np.exp(-t / 0.03))
            env = np.exp(-t / 0.12)
            a = int(0.003 * rate)
            env[:a] *= np.linspace(0, 1, a)
            parts.append(bell * env)
        wave_ = np.concatenate(parts)
    elif name == "hit":
        # A short "tock": two high tones that die away in a few ms, plus a tiny
        # noise click at the start, like a ball on a paddle.
        n = int(rate * 0.09)
        t = np.arange(n) / rate
        rng = np.random.default_rng(193)
        wave_ = (np.sin(2 * np.pi * 1050 * t) * np.exp(-t / 0.018)
                 + 0.5 * np.sin(2 * np.pi * 2300 * t) * np.exp(-t / 0.008)
                 + 0.4 * rng.uniform(-1, 1, n) * np.exp(-t / 0.002))
    else:
        out = []
        notes = SONGS[name]
        for i, (note, dur) in enumerate(notes):
            n = int(rate * dur)
            t = np.arange(n) / rate
            hz = note_hz(note)
            if hz == 0:
                out.append(np.zeros(n))
                continue
            if name == "death" and i == len(notes) - 1:
                phase = 2 * np.pi * (hz * t + (6 / (2 * np.pi * 5)) * np.sin(2 * np.pi * 5 * t))
            else:
                phase = 2 * np.pi * hz * t
            # A few harmonics make it sound less like a test tone; a short
            # attack/release envelope avoids clicks between notes.
            w = np.sin(phase) + 0.35 * np.sin(2 * phase) + 0.15 * np.sin(3 * phase)
            env = np.ones(n)
            a, r = min(n, int(0.01 * rate)), min(n, int(0.06 * rate))
            env[:a] = np.linspace(0, 1, a)
            env[n - r:] *= np.linspace(1, 0, r)
            out.append(w * env)
        wave_ = np.concatenate(out)
    return PEAK.get(name, 0.9) * wave_ / np.max(np.abs(wave_))


def write_wav(path: Path, samples: np.ndarray, rate: int):
    """Float samples (-1..1) -> 16-bit mono .wav."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())


def read_wav(path: Path, rate: int) -> np.ndarray:
    """A .wav file -> mono float samples (-1..1) at `rate` (linear resampling)."""
    with wave.open(str(path), "rb") as w:
        width, channels, file_rate = w.getsampwidth(), w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width == 1:
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128
    elif width == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768
    elif width == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648
    else:
        raise ValueError(f"{width * 8}-bit audio not supported (use 8, 16 or 32-bit PCM)")
    x = x.reshape(-1, channels).mean(axis=1)            # stereo -> mono
    if file_rate != rate and len(x) > 1:
        n = int(len(x) * rate / file_rate)
        x = np.interp(np.arange(n) * file_rate / rate, np.arange(len(x)), x)
    return x.astype(np.float32)


def load(name: str, rate: int) -> np.ndarray:
    """The sound's file if there is one (and it reads), else the synthesized one."""
    path = SOUND_DIR / f"{name}.wav"
    if path.exists():
        try:
            return read_wav(path, rate)
        except (wave.Error, ValueError, EOFError) as e:
            print(f"sounds: can't read {path.name} ({e}); using the built-in {name} sound")
    return synthesize(name, rate).astype(np.float32)


class SoundRules:
    """Decides which sounds a frame's game events get (the table at the top).
    No audio in here, so the rules can be tested exactly."""

    def __init__(self, best_streak: int = 0):
        self.old_record = best_streak   # the record when the current streak started
        self.chimed = False             # "record" chime already played this rally?

    def decide(self, events: list, game, streak_before: int) -> list:
        """[(sound name, reason)] for this frame. `game` is the GameState after
        the frame; streak_before = game.streak before it (a miss resets it)."""
        import game_state as gs
        out = []
        # A record that went up without hits (it loaded from MQTT) is the
        # record to beat; one that went down (tag-5 reset) restarts the count.
        if gs.EVENT_BEST_RESET in events:
            self.old_record, self.chimed = game.best_streak, False
        elif game.best_streak > max(self.old_record, game.streak, streak_before):
            self.old_record = game.best_streak
        minimum = config.MIN_RECORD_FOR_HIGH_SCORE
        for _ in range(events.count(gs.EVENT_BOUNCE)):
            side = "my" if game.last_bounce and game.last_bounce[1] < config.TABLE_LENGTH / 2 else "their"
            out.append(("tick", f"ball bounced on {side} side"))
        if gs.EVENT_HIT in events:
            out.append(("hit", f"paddle hit, volley {game.streak}"))
            if not self.chimed and game.streak > self.old_record and game.streak >= minimum:
                out.append(("record", f"HIGH SCORE: volley {game.streak} passed the record "
                                      f"(old {self.old_record})"))
                self.chimed = True
        if gs.EVENT_LIFE_USED in events:
            out.append(("life", f"EXTRA LIFE card: the miss is undone, volley {game.streak} goes on"))
        if gs.EVENT_READY_COUNT in events:
            out.append(("count", f"serve countdown {game.ready_number}"))
        if gs.EVENT_GO in events:
            out.append(("go", "GO: the ball is served"))
        if gs.EVENT_SLOWMO in events:
            out.append(("slowmo", f"SLOW-MO card: {config.SLOWMO_SPEED:.0%} speed for "
                                  f"{config.SLOWMO_BALLS} balls"))
        if gs.EVENT_COUNTDOWN in events:
            out.append(("count", f"extra life countdown {game.countdown_number}"))
        if gs.EVENT_MISS in events:     # the rally ended (after any countdown)
            ended = streak_before
            why = (game.miss_text or "miss").split("\n")[0].lower()
            if ended:
                why += f", {ended} volleys"
                if ended > self.old_record:
                    why += f" (new record, old {self.old_record})"
            out.append(("death", f"player miss, {why}"))
            self.old_record = max(self.old_record, game.best_streak)
            self.chimed = False
        return out


class SoundPlayer:
    """Plays the game's sounds on a background thread. play() never blocks."""

    def __init__(self, muted: bool = None):
        self.muted = config.SOUND_MUTED if muted is None else muted
        self.error = None                # set if the audio device failed (game stays silent)
        self._queue = queue.Queue()      # names to play, from the game thread
        self._stop = threading.Event()
        self._thread = None
        if not self.muted:
            self._thread = threading.Thread(target=self._run, name="audio", daemon=True)
            self._thread.start()

    def play(self, name: str):
        if self._thread is not None and self.error is None:
            self._queue.put(name)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._queue.put(None)        # wake the thread up
            self._thread.join(timeout=1.0)

    # ------------------------------------------------------------ audio thread
    def _run(self):
        import pyaudio                   # only needed when sound is on
        pa = stream = None
        try:
            pa = pyaudio.PyAudio()
            rate = self._output_rate(pa, pyaudio)
            sounds = {name: load(name, rate) for name in NAMES}
            stream = pa.open(format=pyaudio.paFloat32, channels=1, rate=rate, output=True,
                             frames_per_buffer=SOUND_CHUNK)
        except Exception as e:           # no speaker / PortAudio error: play on silently
            self.error = f"sound off: {e}"
            print(f"sounds: {self.error}")
            if pa is not None:
                pa.terminate()
            return

        playing = []                     # [samples, position] of each sound in progress
        try:
            while not self._stop.is_set():
                # Wait for a sound when nothing is playing; otherwise just
                # pick up any new ones between chunks.
                try:
                    names = [self._queue.get(timeout=0.2 if not playing else 0)]
                except queue.Empty:
                    names = []
                while not self._queue.empty():
                    names.append(self._queue.get_nowait())
                for name in names:
                    if name is None:
                        continue
                    if name in END_OF_STREAK:
                        playing.clear()  # the streak is over: this plays alone
                    playing.append([sounds[name], 0])
                if not playing:
                    continue
                # Mix one chunk of everything that's playing
                chunk = np.zeros(SOUND_CHUNK, np.float32)
                for item in playing:
                    data, pos = item
                    part = data[pos:pos + SOUND_CHUNK]
                    chunk[:len(part)] += part
                    item[1] = pos + SOUND_CHUNK
                playing = [p for p in playing if p[1] < len(p[0])]
                chunk = np.clip(chunk * config.SOUND_VOLUME, -1, 1)
                stream.write(chunk.tobytes())   # blocks ~one chunk: this paces the loop
        except Exception as e:
            print(f"sounds: playback stopped ({e!r})")
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()

    @staticmethod
    def _output_rate(pa, pyaudio) -> int:
        """SOUND_SAMPLE_RATE if the speaker takes it, else its own default rate."""
        idx = pa.get_default_output_device_info()["index"]
        default = int(pa.get_device_info_by_index(idx)["defaultSampleRate"])
        for rate in (config.SOUND_SAMPLE_RATE, default):
            try:
                if pa.is_format_supported(rate, output_device=idx, output_channels=1,
                                          output_format=pyaudio.paFloat32):
                    return rate
            except ValueError:
                pass
        return default


if __name__ == "__main__":
    import time
    if "--write" in sys.argv:
        for name in NAMES:
            path = SOUND_DIR / f"{name}.wav"
            if path.exists() and "--force" not in sys.argv:
                print(f"{path.relative_to(REPO_ROOT)} exists (use --force to overwrite)")
                continue
            write_wav(path, synthesize(name, config.SOUND_SAMPLE_RATE), config.SOUND_SAMPLE_RATE)
            print(f"wrote {path.relative_to(REPO_ROOT)}")
    else:
        player = SoundPlayer(muted=False)
        for name in NAMES:
            print(f"playing {name}")
            player.play(name)
            time.sleep(len(load(name, 44100)) / 44100 + 0.4)
        player.stop()
