"""
calibration.py
---------------
Maps a pixel coordinate from the overhead camera photo to a real-world
(x, y) position in centimeters, relative to the arm's base.

Since the camera looks straight down (bird's-eye), a single homography
(perspective transform) computed from a handful of known point pairs is
enough -- it holds for the whole table plane, no per-cube correction
needed.

Until you've run calibrate.py at least once, pixel_to_arm() is an
identity passthrough, so the rest of the pipeline (sorter.py, the
dashboard) keeps working exactly as it does today, just without real
units -- matching the placeholder behavior sorter.py already documents.
"""

import json
import os

import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CALIB_PATH = os.path.join(BASE_DIR, "calibration.json")


def load_calibration():
    """Returns the saved 3x3 homography as a numpy array, or None if
    calibrate.py hasn't been run yet."""
    if not os.path.exists(CALIB_PATH):
        return None
    with open(CALIB_PATH) as f:
        data = json.load(f)
    return np.array(data["homography"], dtype=np.float64)


def save_calibration(pixel_points, world_points):
    """
    pixel_points: list of >=4 (x, y) pixel coords clicked on the overhead photo
    world_points: matching list of (x, y) REAL-WORLD cm coords, measured with
                  a ruler/tape relative to the arm's base at (0, 0)
    """
    import cv2  # imported here so calibration.py has no hard cv2 dependency
    # for callers that only need pixel_to_arm() before calibration exists

    H, _ = cv2.findHomography(
        np.array(pixel_points, dtype=np.float64),
        np.array(world_points, dtype=np.float64),
    )
    if H is None:
        raise ValueError("Could not compute a homography from the given points -- "
                          "check that the points aren't collinear and there are no typos.")
    with open(CALIB_PATH, "w") as f:
        json.dump({"homography": H.tolist()}, f, indent=2)
    return H


def is_calibrated():
    return os.path.exists(CALIB_PATH)


def pixel_to_arm(px):
    """
    px: (x, y) pixel coordinate, e.g. a cube's centroid_px from detect_cubes.

    Returns (x_cm, y_cm) relative to the arm base if calibrated, otherwise
    returns px unchanged (identity fallback) so the pipeline still runs
    before calibration is done.
    """
    H = load_calibration()
    if H is None:
        return px

    x, y = px
    vec = np.array([x, y, 1.0])
    mapped = H @ vec
    mapped = mapped / mapped[2]
    return (float(mapped[0]), float(mapped[1]))
