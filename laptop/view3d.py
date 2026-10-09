"""First-person view: turns table coordinates (meters) into screen pixels.

A simple pinhole camera stands behind my end of the table at eye height and
looks straight along the table (see VIEW_* in config.py). A point at depth d
in front of the camera lands at
    screen_x = center_x + f * (x - cam_x) / d
    screen_y = horizon_y - f * (y - cam_y) / d
so far things shrink toward the horizon (perspective), and a size s at depth
d is f * s / d pixels.
"""
import config


def depth(z: float) -> float:
    """Distance in front of the camera (always > 0 for the visible world)."""
    return max(z - config.VIEW_CAM_Z, 0.05)


def project(x: float, y: float, z: float):
    """World (m) -> screen (px). Returns (sx, sy)."""
    d = depth(z)
    sx = config.WINDOW_WIDTH / 2 + config.VIEW_FOCAL_PX * (x - config.VIEW_CAM_X) / d
    sy = config.VIEW_HORIZON_Y - config.VIEW_FOCAL_PX * (y - config.VIEW_CAM_Y) / d
    return sx, sy


def size(meters: float, z: float) -> float:
    """How many pixels `meters` covers at depth z."""
    return config.VIEW_FOCAL_PX * meters / depth(z)
