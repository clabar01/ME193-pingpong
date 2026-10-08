"""Template for tools/board_secrets.py. This file is safe to share.

advertise_board.py copies this to board_secrets.py (which Git ignores) on its
first run; edit that copy with your board's values.
"""

# Board name and WiFi IP, used to advertise the board over mDNS so App Lab
# can find it. Find the IP by running on the board:  hostname -I
BOARD_NAME = "unoq"
BOARD_IP = "192.168.1.50"
