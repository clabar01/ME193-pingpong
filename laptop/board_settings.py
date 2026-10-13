"""The board's settings from the single settings file,
~/ArduinoApps/tools/board_secrets.py (see CLAUDE.md: one place to change the IP).
"""
import importlib.util
from pathlib import Path

SECRETS = Path.home() / "ArduinoApps" / "tools" / "board_secrets.py"


def _load():
    try:
        spec = importlib.util.spec_from_file_location("board_secrets", SECRETS)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def board_ip():
    """BOARD_IP from board_secrets.py, or None if the file is missing/broken."""
    return getattr(_load(), "BOARD_IP", None)


def board_hosts() -> list:
    """Every address the board may have: BOARD_IP first, then HOSTS (campus,
    home, AirFour.local), without repeats. Empty if the file is missing."""
    module = _load()
    hosts = [getattr(module, "BOARD_IP", None)] + list(getattr(module, "HOSTS", []))
    return list(dict.fromkeys(h for h in hosts if h))
