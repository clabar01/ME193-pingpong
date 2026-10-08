"""Advertise the Arduino UNO Q over mDNS so App Lab can find it (e.g. on Tufts
WiFi, where the board doesn't show up by itself). Equivalent to:
dns-sd -P "<BOARD_NAME>" _arduino._tcp local 80 <BOARD_NAME>.local <BOARD_IP> board=unoq ...

Adapted from the professor's Tufts_WiFi.py. BOARD_NAME and BOARD_IP come from
the single board settings file, ~/ArduinoApps/tools/board_secrets.py (the same
one deploy.py uses), so the IP only ever needs changing there.

Run from the repo root:  python tools/advertise_board.py   (Ctrl+C to stop)
Requires: pip install zeroconf
"""
import importlib.util
import socket
import sys
import time
from pathlib import Path

from zeroconf import ServiceInfo, Zeroconf

SECRETS = Path.home() / "ArduinoApps" / "tools" / "board_secrets.py"

if not SECRETS.exists():
    sys.exit(f"Board settings not found: {SECRETS}\n"
             "Clone github.com/clabar01/ArduinoApps to ~/ArduinoApps and run\n"
             "  python3 tools/deploy.py\n"
             "there once to create it, then set BOARD_NAME and BOARD_IP in it.")

# Load that file by path (it isn't on sys.path and isn't part of this repo).
_spec = importlib.util.spec_from_file_location("board_secrets", SECRETS)
_secrets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_secrets)
BOARD_NAME = _secrets.BOARD_NAME
BOARD_IP = _secrets.BOARD_IP

info = ServiceInfo(
    type_="_arduino._tcp.local.",
    name=f"{BOARD_NAME}._arduino._tcp.local.",
    addresses=[socket.inet_aton(BOARD_IP)],
    port=80,
    server=f"{BOARD_NAME}.local.",
    properties={
        "board": "unoq",
        "vid": "0x2341",
        "pid": "0x0078",
        "vid.0": "0x2341",
        "pid.0": "0x0078",
    },
)

zc = Zeroconf()
zc.register_service(info)
print(f"Advertising {BOARD_NAME} ({BOARD_IP}). Press Ctrl+C to stop.")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
finally:
    zc.unregister_service(info)
    zc.close()
