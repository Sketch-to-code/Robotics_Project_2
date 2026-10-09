"""
arm_ik.py
---------
Inverse kinematics for a base-rotate + 2-link planar arm (the geometry
implied by arm_controller.ino: base, shoulder, elbow, gripper channels on
the PCA9685).

Convention used internally (before the config.py offsets/dir flips are
applied):
  - The arm base sits at the origin of the (x, y) plane, x/y in cm.
  - `base_deg`: 0 deg points along +x, increases counter-clockwise (viewed
    from above) toward +y.
  - `shoulder_deg`: 0 deg = shoulder link pointing straight along the
    horizontal (away from the base), increasing = lifting upward.
  - `elbow_deg`: 0 deg = elbow link folded straight back onto the shoulder
    link, 180 deg = fully extended in line with it.

This is a clean, textbook convention -- it will almost never match your
actual servo mounting directly. That's what the OFFSET/DIR constants in
config.py are for. Don't hand-edit the math below to "fix" a real robot;
tune the offsets instead, using test_arm_angles.py.
"""

import math

import config


class UnreachableError(ValueError):
    pass


def solve(x_cm, y_cm, z_cm):
    """
    x_cm, y_cm: target position on the table plane, in cm, relative to the
                arm's base (origin). Comes from calibration.pixel_to_arm().
    z_cm: height of the target, in cm, in the same frame as
          config.BASE_HEIGHT_CM (0 = table surface).

    Returns (base_deg, shoulder_deg, elbow_deg) as servo-ready angles
    (0-180), already passed through the config.py offsets/directions.
    Raises UnreachableError if the target is outside the arm's workspace.
    """
    L1 = config.LINK_SHOULDER_ELBOW_CM
    L2 = config.LINK_ELBOW_GRIPPER_CM
    H = config.BASE_HEIGHT_CM

    r = math.hypot(x_cm, y_cm)
    base_rad = math.atan2(y_cm, x_cm)

    dz = H - z_cm  # vertical drop from the shoulder pivot down to the target
    D = math.hypot(r, dz)

    if D > (L1 + L2) or D < abs(L1 - L2):
        raise UnreachableError(
            f"Target ({x_cm:.1f}, {y_cm:.1f}, {z_cm:.1f}) cm is out of reach "
            f"(needs D={D:.1f}cm, arm spans {abs(L1 - L2):.1f}-{L1 + L2:.1f}cm). "
            f"Check calibration or LINK_*_CM in config.py."
        )

    cos_elbow = (L1 ** 2 + L2 ** 2 - D ** 2) / (2 * L1 * L2)
    cos_elbow = max(-1.0, min(1.0, cos_elbow))
    elbow_interior_rad = math.acos(cos_elbow)

    cos_shoulder_tri = (L1 ** 2 + D ** 2 - L2 ** 2) / (2 * L1 * D)
    cos_shoulder_tri = max(-1.0, min(1.0, cos_shoulder_tri))
    shoulder_tri_rad = math.acos(cos_shoulder_tri)
    shoulder_rad = math.atan2(dz, r) + shoulder_tri_rad

    base_deg_raw = math.degrees(base_rad)
    shoulder_deg_raw = math.degrees(shoulder_rad)
    elbow_deg_raw = 180.0 - math.degrees(elbow_interior_rad)

    base_deg = config.BASE_OFFSET_DEG + config.BASE_DIR * base_deg_raw
    shoulder_deg = config.SHOULDER_OFFSET_DEG + config.SHOULDER_DIR * shoulder_deg_raw
    elbow_deg = config.ELBOW_OFFSET_DEG + config.ELBOW_DIR * elbow_deg_raw

    for name, val in (("base", base_deg), ("shoulder", shoulder_deg), ("elbow", elbow_deg)):
        if not (0 <= val <= 180):
            raise UnreachableError(
                f"Computed {name} angle {val:.1f} deg is outside the servo's 0-180 range. "
                f"Likely a geometry/offset mismatch in config.py, not a real out-of-reach target."
            )

    if not (config.SHOULDER_MIN_SAFE_DEG <= shoulder_deg <= config.SHOULDER_MAX_SAFE_DEG):
        raise UnreachableError(
            f"Computed shoulder angle {shoulder_deg:.1f} deg is outside the mechanically-safe "
            f"range ({config.SHOULDER_MIN_SAFE_DEG}-{config.SHOULDER_MAX_SAFE_DEG} deg) found "
            f"during physical testing -- refusing to send it to avoid grinding the arm against "
            f"its own structure. This target is effectively out of reach."
        )
    if not (config.ELBOW_MIN_SAFE_DEG <= elbow_deg <= config.ELBOW_MAX_SAFE_DEG):
        raise UnreachableError(
            f"Computed elbow angle {elbow_deg:.1f} deg is outside the mechanically-safe "
            f"range ({config.ELBOW_MIN_SAFE_DEG}-{config.ELBOW_MAX_SAFE_DEG} deg) found "
            f"during physical testing -- refusing to send it to avoid grinding the arm against "
            f"its own structure. This target is effectively out of reach."
        )

    return round(base_deg, 1), round(shoulder_deg, 1), round(elbow_deg, 1)
