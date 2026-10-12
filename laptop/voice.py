"""Voice commands: offline speech recognition with Vosk, in a background thread.

HOW IT WORKS (for the write-up)
  Vosk is an offline speech recognizer (Kaldi inside): a small acoustic model
  turns short slices of microphone sound into the likely speech sounds, and a
  decoder searches for the word sequence that best explains them. We give the
  decoder a tiny grammar, only the game's commands, so it can only answer
  with one of them or "[unk]" (unknown).

  Because a grammar decoder always picks the CLOSEST allowed phrase (in a test
  "play the game" came out as "play again"), a command only counts if:
    1. the grammar recognizer heard exactly that one phrase, with nothing
       unknown around it ("smashing pumpkins" -> "smash [unk]" is ignored), AND
    2. for the STRICT commands (VOICE_STRICT_COMMANDS: play again, level
       one/two/three, pause - the ones that would disrupt the game), a second
       recognizer on the same audio, with no grammar (free English), heard
       every word of the command (each word at least VOICE_WORD_MATCH
       similar), so "play the game" (no "again") is ignored. smash, resume
       and score skip check 2: it fails often in a noisy room, and a wrong
       one costs little (they also only act at the right moment in the game).
  Then a VOICE_COOLDOWN_S cooldown per command, so one phrase triggers once.

  The game's own sounds must not trigger commands: while a longer game sound
  is playing (sounds.SoundPlayer.voice_mute_until) or the laptop is speaking
  (speech.Speaker.busy), the mic audio is not fed to the recognizers at all,
  and they are reset so no half-heard word carries over.

Mic and speakers don't conflict: the mic is read by its own PortAudio input
stream in this thread; sounds.py plays through a separate output stream in
its own thread; text-to-speech runs in its own process (speech.py).

Commands come out of poll() as (command, heard text, time). Every decision
(accepted, or ignored and why) is kept in .log for the events log.
"""
import difflib
import json
import queue
import threading
import time
from pathlib import Path

import config

REPO_ROOT = Path(__file__).resolve().parent.parent
# Words the free recognizer may write for a command word
ALIASES = {"one": {"won", "1"}, "two": {"to", "too", "2"}, "three": {"tree", "3"}}


def words_match(command: str, free_text: str) -> bool:
    """Check 2: every word of the command is (nearly) in the free transcript."""
    heard = free_text.split()
    for word in command.split():
        ok = any(h in ALIASES.get(word, ()) or difflib.SequenceMatcher(None, word, h).ratio()
                 >= config.VOICE_WORD_MATCH for h in heard)
        if not ok:
            return False
    return True


def collapse_repeats(said: str) -> str:
    """One command said several times in a row counts once: "smash smash" ->
    "smash". Anything else is returned unchanged."""
    words = said.split()
    for command in config.VOICE_COMMANDS:
        n = len(command.split())
        if len(words) % n == 0 and all(" ".join(words[i:i + n]) == command for i in range(0, len(words), n)):
            return command
    return said


class CommandMatcher:
    """Two Vosk recognizers on the same audio; feed() returns decisions.
    No audio device in here, so it can be tested on WAV files."""

    def __init__(self, model, rate: int = config.VOICE_SAMPLE_RATE):
        from vosk import KaldiRecognizer
        grammar = json.dumps(list(config.VOICE_COMMANDS) + ["[unk]"])
        self.grammar = KaldiRecognizer(model, rate, grammar)   # only the commands
        self.free = KaldiRecognizer(model, rate)                # any English
        self._free_heard = []      # (time, text) of the free recognizer's recent results

    def reset(self):
        self.grammar.Reset()
        self.free.Reset()
        self._free_heard.clear()

    def feed(self, pcm16: bytes, now: float) -> list:
        """16-bit mono audio at the model's rate. Returns a list of
        (command or None, grammar text, free text, accepted, reason)."""
        if self.free.AcceptWaveform(pcm16):
            text = json.loads(self.free.Result()).get("text", "")
            if text:
                self._free_heard.append((now, text))
        if not self.grammar.AcceptWaveform(pcm16):
            return []
        said = json.loads(self.grammar.Result()).get("text", "").strip()
        if not said or said == "[unk]":
            return []
        said = collapse_repeats(said)        # "smash smash" (said twice, excited) -> "smash"
        recent = [t for when, t in self._free_heard if now - when < 2.0]
        free_text = " ".join(recent + [json.loads(self.free.PartialResult()).get("partial", "")]).strip()
        self._free_heard.clear()
        if said not in config.VOICE_COMMANDS:
            return [(None, said, free_text, False, "not exactly one command")]
        if said in config.VOICE_STRICT_COMMANDS and not words_match(said, free_text):
            return [(said, said, free_text, False, "strict command: free recognizer didn't hear it")]
        return [(said, said, free_text, True, "")]


class VoiceListener:
    """Reads the mic in a background thread and turns speech into commands.

    status: "loading", "listening", "muted" (a game sound / speech is playing),
    "off" (--no-voice), "no mic", "no model". last: (command, time) of the
    last accepted command, for the screen.
    """

    def __init__(self, enabled: bool = True, mute_sources=()):
        self.mute_sources = list(mute_sources)   # callables -> True while the game makes sound
        self.status = "loading" if enabled else "off"
        self.last = None
        self.log = queue.Queue()                 # (time, text) decisions for the events log
        self._commands = queue.Queue()
        self._last_fired = {}
        self._stop = threading.Event()
        self._thread = None
        if enabled:
            self._thread = threading.Thread(target=self._run, name="voice", daemon=True)
            self._thread.start()

    def poll(self) -> list:
        """Commands heard since the last poll: [(command, heard text, time)]."""
        out = []
        while not self._commands.empty():
            out.append(self._commands.get_nowait())
        return out

    def poll_log(self) -> list:
        out = []
        while not self.log.empty():
            out.append(self.log.get_nowait())
        return out

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)

    def muted(self) -> bool:
        return any(src() for src in self.mute_sources)

    # ------------------------------------------------------------ thread
    def _run(self):
        try:
            from vosk import Model, SetLogLevel
            SetLogLevel(-1)
            path = REPO_ROOT / config.VOICE_MODEL_PATH
            if not path.exists():
                self.status = "no model"
                print(f"voice: no Vosk model at {config.VOICE_MODEL_PATH} (see README); voice commands off")
                return
            matcher = CommandMatcher(Model(str(path)))
        except Exception as e:
            self.status = "no model"
            print(f"voice: can't load Vosk ({e}); voice commands off")
            return

        import numpy as np
        import pyaudio
        pa = stream = None
        try:
            pa = pyaudio.PyAudio()
            info = pa.get_default_input_device_info()
            rate = self._input_rate(pa, pyaudio, int(info["index"]), int(info["defaultSampleRate"]))
            chunk = int(rate * 0.1)                     # 100 ms per read
            stream = pa.open(format=pyaudio.paInt16, channels=1, rate=rate, input=True,
                             frames_per_buffer=chunk)
        except Exception as e:                          # no microphone: keep playing without voice
            self.status = "no mic"
            print(f"voice: no microphone found ({e}); voice commands off, the game goes on")
            if pa is not None:
                pa.terminate()
            return

        self.status = "listening"
        was_muted = False
        try:
            while not self._stop.is_set():
                data = stream.read(chunk, exception_on_overflow=False)
                now = time.monotonic()
                if self.muted():
                    # The game is making sound: don't listen; forget any half-heard word.
                    if not was_muted:
                        matcher.reset()
                    was_muted, self.status = True, "muted"
                    continue
                was_muted, self.status = False, "listening"
                if rate != config.VOICE_SAMPLE_RATE:      # resample to the model's rate
                    x = np.frombuffer(data, np.int16).astype(np.float32)
                    n = int(len(x) * config.VOICE_SAMPLE_RATE / rate)
                    x = np.interp(np.arange(n) * rate / config.VOICE_SAMPLE_RATE, np.arange(len(x)), x)
                    data = x.astype(np.int16).tobytes()
                for cmd, said, free, ok, why in matcher.feed(data, now):
                    self._decide(cmd, said, free, ok, why, now)
        except Exception as e:
            print(f"voice: microphone stopped ({e!r}); voice commands off")
            self.status = "no mic"
        finally:
            stream.close()
            pa.terminate()

    def _decide(self, cmd, said, free, ok, why, now):
        if ok and now - self._last_fired.get(cmd, -1e9) < config.VOICE_COOLDOWN_S:
            ok, why = False, "cooldown"
        if ok:
            self._last_fired[cmd] = now
            self.last = (cmd, now)
            self._commands.put((cmd, free, now))
            self.log.put((now, f"VOICE heard {cmd!r} (free recognizer: {free!r})"))
        else:
            self.log.put((now, f"VOICE ignored {said!r}: {why} (free recognizer: {free!r})"))

    @staticmethod
    def _input_rate(pa, pyaudio, index: int, default: int) -> int:
        """16 kHz if the mic supports it, else its own rate (resampled)."""
        for rate in (config.VOICE_SAMPLE_RATE, default):
            try:
                if pa.is_format_supported(rate, input_device=index, input_channels=1,
                                          input_format=pyaudio.paInt16):
                    return rate
            except ValueError:
                pass
        return default
