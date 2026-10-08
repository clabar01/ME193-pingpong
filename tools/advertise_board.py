"""Advertise the Arduino UNO Q over mDNS so App Lab can find it (e.g. on Tufts
WiFi, where the board doesn't show up by itself). Equivalent to:
dns-sd -P "<BOARD_NAME>" _arduino._tcp local 80 <BOARD_NAME>.local <BOARD_IP> board=unoq ...

Adapted from the professor's Tufts_WiFi.py. BOARD_NAME and BOARD_IP come from
tools/board_secrets.py (git-ignored), created from board_secrets_example.py on
first run.

Run from the repo root:  python tools/advertise_board.py   (Ctrl+C to stop)
Requires: pip install zeroconf
"""
import shutil
import socket
import sys
import time
from pathlib import Path

from zeroconf import ServiceInfo, Zeroconf

_here = Path(__file__).resolve().parent
_secrets = _here / "board_secrets.py"
if not _secrets.exists():
    shutil.copy(_here / "board_secrets_example.py", _secrets)
    sys.exit(f"Created {_secrets}. Edit BOARD_NAME and BOARD_IP, then run again.")

sys.path.insert(0, str(_here))
from board_secrets import BOARD_IP, BOARD_NAME  # noqa: E402

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
