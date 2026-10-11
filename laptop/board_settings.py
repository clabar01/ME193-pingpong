"""The board's settings from the single settings file,
~/ArduinoApps/tools/board_secrets.py (see CLAUDE.md: one place to change the IP).
"""
import importlib.util
from pathlib import Path

SECRETS = Path.home() / "ArduinoApps" / "tools" / "board_secrets.py"


def board_ip():
    """BOARD_IP from board_secrets.py, or None if the file is missing/broken."""
    try:
        spec = importlib.util.spec_from_file_location("board_secrets", SECRETS)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.BOARD_IP
    except Exception:
        return None
