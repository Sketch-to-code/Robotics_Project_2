"""
test_arm_angles.py
-------------------
Manual jog tool for the arm. Run this once the ESP32 is flashed and on
your WiFi, BEFORE trusting arm_ik.py's output. Use it to:

  1. Confirm the dashboard can actually reach the ESP32.
  2. Work out BASE_OFFSET_DEG/DIR, SHOULDER_OFFSET_DEG/DIR, and
     ELBOW_OFFSET_DEG/DIR in config.py, by watching which physical
     direction each joint moves as you increase its angle.

Usage:
    python test_arm_angles.py
    (make sure settings.json has arm_ip set first, e.g. via the dashboard
    Settings panel, or by editing settings.json directly)

Then type commands like:
    base 90
    shoulder 45
    elbow 120
    gripper 30
    goto 10 0 0
    status
    quit

'goto x y z' sends the gripper to that (x, y, z) position in cm, relative
to the arm's base -- via arm_ik.py's inverse kinematics, not raw servo
angles. Use this with a ruler-measured target to check whether the math
actually points the arm where you tell it to, BEFORE any camera
calibration exists. Start with y=0 (straight in front of the base) to
isolate and verify the base angle first, then vary x/z to check
shoulder/elbow, then finally try a nonzero y to verify base rotation
direction.
"""

from arm_client import ArmClient, ArmConnectionError
from arm_ik import UnreachableError
import config

VALID_JOINTS = {"base", "shoulder", "elbow", "gripper"}


def main():
    arm = ArmClient()
    print("Connected to arm client. Commands: <joint> <angle 0-180>, 'status', 'quit'")
    print(f"Valid joints: {', '.join(sorted(VALID_JOINTS))}\n")

    while True:
        try:
            line = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if not line:
            continue
        if line in ("quit", "exit"):
            break
        if line == "status":
            try:
                print(arm.status())
            except ArmConnectionError as e:
                print(f"Error: {e}")
            continue

        if line.startswith("goto"):
            parts = line.split()
            if len(parts) != 4:
                print("Usage: goto x_cm y_cm z_cm  (e.g. 'goto 10 0 0')")
                continue
            try:
                x_cm, y_cm, z_cm = (float(p) for p in parts[1:])
            except ValueError:
                print("x, y, z must be numbers.")
                continue
            try:
                arm.move_to((x_cm, y_cm), z_cm)
                print(f"Moved to ({x_cm}, {y_cm}, {z_cm}) cm -- check the physical result "
                      f"against where you measured/expected it to land.")
            except (ArmConnectionError, UnreachableError) as e:
                print(f"Error: {e}")
            continue

        parts = line.split()
        if len(parts) != 2 or parts[0] not in VALID_JOINTS:
            print(f"Didn't understand that. Try e.g. 'base 90', or 'status', or 'quit'.")
            continue

        joint, angle_str = parts
        try:
            angle = int(angle_str)
        except ValueError:
            print("Angle must be a whole number 0-180.")
            continue
        if not (0 <= angle <= 180):
            print("Angle must be between 0 and 180.")
            continue

        SAFE_RANGES = {
            "shoulder": (config.SHOULDER_MIN_SAFE_DEG, config.SHOULDER_MAX_SAFE_DEG),
            "elbow": (config.ELBOW_MIN_SAFE_DEG, config.ELBOW_MAX_SAFE_DEG),
            "gripper": (config.GRIPPER_MIN_SAFE_DEG, config.GRIPPER_MAX_SAFE_DEG),
        }
        if joint in SAFE_RANGES:
            lo, hi = SAFE_RANGES[joint]
            if not (lo <= angle <= hi):
                print(f"REFUSED: {joint} {angle} is outside the safe range ({lo}-{hi} deg). "
                      f"Going further risks grinding the arm against its own structure or "
                      f"straining the motor.")
                continue

        try:
            result = arm.move_all(**{joint: angle})
            print(result)
        except ArmConnectionError as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    main()
