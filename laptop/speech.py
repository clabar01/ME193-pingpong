"""Text-to-speech (pyttsx3) for the "score" voice command, without blocking the game.

pyttsx3 on macOS only speaks from a process's MAIN thread (in a background
thread runAndWait() returns at once and nothing is heard), and in the game's
main thread it would freeze the game for the whole sentence. So the speech
runs in its own small process, `python laptop/speech.py --serve`, where
pyttsx3 has the main thread to itself. The game writes one line of text per
sentence to its stdin; it answers "start" / "done" on stdout, so the voice
listener knows when the laptop itself is talking (and ignores the mic then).
If pyttsx3 can't start, the helper falls back to the macOS `say` command.
"""
import subprocess
import sys
import threading
import time
from pathlib import Path

import config


class Speaker:
    def __init__(self, enabled: bool = True):
        self.speaking = False
        self.quiet_until = 0.0         # time.monotonic(): the mic may listen again after this
        self.error = None
        self._proc = None
        if not enabled:
            return
        try:
            self._proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve"],
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
            threading.Thread(target=self._read, name="speech", daemon=True).start()
        except OSError as e:
            self.error = f"text-to-speech off: {e}"
            print(self.error)

    def say(self, text: str):
        """Speak in the background (never blocks the game)."""
        if self._proc is None or self._proc.poll() is not None:
            return
        self.speaking = True           # mute the mic right away, before the helper answers
        try:
            self._proc.stdin.write(text.replace("\n", " ") + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self.speaking = False

    @property
    def busy(self) -> bool:
        return self.speaking or time.monotonic() < self.quiet_until

    def stop(self):
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
                self._proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                self._proc.kill()

    def _read(self):
        for line in self._proc.stdout:
            word = line.strip()
            if word == "start":
                self.speaking = True
            elif word == "done":
                self.speaking = False
                self.quiet_until = time.monotonic() + config.VOICE_MUTE_TAIL_S


def serve():
    """The helper process: speak each stdin line with pyttsx3 (or `say`)."""
    engine = None
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", config.TTS_RATE)
    except Exception as e:   # no pyttsx3 / no driver: use the macOS say command
        print(f"pyttsx3 unavailable ({e}); using say", file=sys.stderr)
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        print("start", flush=True)
        if engine is not None:
            engine.say(text)
            engine.runAndWait()
        else:
            subprocess.run(["say", "-r", str(config.TTS_RATE), text])
        print("done", flush=True)


if __name__ == "__main__":
    if "--serve" in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        serve()
