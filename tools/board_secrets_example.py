"""Reference only: this repo does NOT read a board_secrets.py of its own.

The single settings file for the board is ~/ArduinoApps/tools/board_secrets.py
(git-ignored there, created by running ArduinoApps' tools/deploy.py once).
tools/advertise_board.py reads BOARD_NAME and BOARD_IP from it, so your IP is
changed in one place only. It should contain at least:
"""

HOSTS = ["10.247.137.172", "192.168.1.185", "AirFour.local"]  # used by deploy.py
USER = "arduino"
BOARD_NAME = "AirFour"
BOARD_IP = "192.168.1.185"   # the only value to change between home and campus
