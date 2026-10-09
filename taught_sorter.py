"""
taught_sorter.py
----------------
Replays the arm motion you taught by hand with test_arm_angles.py (raw servo
angles, no inverse kinematics). Used by app.py's handoff endpoints.

Taught sequence for the centre slot (slot 1), all at base 56, gripper 100 = open:
    home (shoulder 60, elbow 35) -> shoulder 80 -> elbow 80 -> close gripper on the cube
    -> elbow 35 -> shoulder 40 -> elbow 65 -> open gripper -> elbow 35 -> shoulder 60

Every slot uses exactly the same motion; only the BASE angle at the drop
differs. The base is rotated only while the arm is raised (shoulder 40,
elbow 35), then the arm is lowered at the new angle, and rotated back the same way.

Usage (dashboard folder, venv active):
    python taught_sorter.py --show
    python taught_sorter.py --aim 0        # carry-pose check, NO pick: closed gripper to slot 0's drop pose, Enter releases + returns home
    python taught_sorter.py --place 1      # full cycle: pick from the handoff spot -> slot 1
    python taught_sorter.py --home         # after an error / Ctrl+C: raise, rotate back, open gripper, lower to HOME
"""

import argparse
import time

import fixed_sorter          # reuses SlowArm (1-degree steps, Ctrl+C stops within a step)

N_SLOTS = 3

# ---------------------------------------------------------------------
# TAUGHT VALUES (from your test_arm_angles.py session)
# ---------------------------------------------------------------------
HOME = {"base": 56, "shoulder": 60, "elbow": 35, "gripper": 100}

GRIP_OPEN_DEG = 100
# Holding angle while carrying. Measured by hand: the jaws touch the cube around 80, but only
# 75 (the fully-closed value) holds it firmly. 75 means the servo keeps pushing ~5 deg against
# the cube, so it is only closed for the carry (~10-15 s) and released at once -- if the servo
# gets hot or keeps buzzing, stop and let it cool. Grip pads (foam/rubber/tape) on the jaws
# would let you use 77-78 instead.
GRIP_HOLD_DEG = 75
GRAB_SETTLE_SEC = 0.6     # pause after closing the gripper so the jaws bite before the lift starts

PICK_SHOULDER, PICK_ELBOW = 80, 70      # cube on the handoff spot (base 56)
LIFT_ELBOW = 25                       # elbow angle while the arm is raised / carrying
RAISED_SHOULDER = 40                    # shoulder angle while carrying / rotating
DROP_ELBOW = 70                        # lowered pose at the drop (shoulder = RAISED_SHOULDER)

# Base angle for each drop slot (slot 0 = left, slot 2 = right, viewed from behind; a higher
# base angle swings the arm left -- check with --aim).
# Slot 1 is NOT on the pick line (base 56): a cube there sat in the arm's way when it reached
# for the next cube. It is moved to 70, and slot 0 moved from 84 to 98 so the slot 0 / slot 1
# cubes are still ~28 deg apart instead of touching. Approx. sideways distance at the drop
# radius: ~0.13 cm per degree, so 14 deg is only ~1.8 cm -- too tight for cubes.
# Tune: if the arm still brushes a cube in slot 1 when picking, raise slot 1 (and slot 0).
SLOT_BASE_DEG = (98, 70, 28)


def _go(arm, **joints):
    arm._goto(joints)        # SlowArm: split into 1-degree steps, integer angles


def _to_drop_pose(arm, slot):
    """From the raised carrying pose, rotate to the slot and lower to drop height."""
    _go(arm, shoulder=RAISED_SHOULDER)
    _go(arm, base=SLOT_BASE_DEG[slot])
    _go(arm, elbow=DROP_ELBOW)


def _home_from_drop(arm):
    _go(arm, elbow=LIFT_ELBOW)               # raise
    _go(arm, base=HOME["base"])              # rotate back while raised
    _go(arm, shoulder=HOME["shoulder"])


def _at_home(arm, tol=3):
    """True if the arm's tracked pose is (about) HOME, or unknown (the HOME move below then syncs it)."""
    cur = getattr(arm, "cur", None)
    if not cur:
        return True
    return all(cur.get(j) is None or abs(cur[j] - HOME[j]) <= tol for j in HOME)


def recover_home(arm=None):
    """
    Bring the arm back to HOME from wherever it stopped (an error or Ctrl+C mid-cycle), in the same
    raise -> rotate -> lower order as the taught motion so it never sweeps low across the cubes.
    If it is still holding a cube, the cube is released above the pick spot when the gripper opens.
    """
    if arm is None:
        from arm_client import ArmClient
        arm = fixed_sorter.SlowArm(ArmClient())
    _go(arm, elbow=LIFT_ELBOW)               # raise first
    _go(arm, shoulder=RAISED_SHOULDER)
    _go(arm, base=HOME["base"])              # rotate back while raised
    _go(arm, gripper=GRIP_OPEN_DEG)
    _go(arm, shoulder=HOME["shoulder"])
    _go(arm, elbow=HOME["elbow"])


def place_from_handoff(slot, arm=None):
    """Pick the cube sitting on the handoff spot (base 56) and put it in drop slot `slot`."""
    if not 0 <= slot < N_SLOTS:
        raise ValueError(f"slot must be 0..{N_SLOTS - 1}")
    if arm is None:
        from arm_client import ArmClient
        arm = fixed_sorter.SlowArm(ArmClient())

    try:
        if not _at_home(arm):                # an earlier run stopped mid-cycle: do not sweep straight to HOME
            print("Arm is not at HOME (an earlier run stopped?) -- returning it safely first.")
            recover_home(arm)
        _go(arm, **HOME)                     # sync to home (no motion if it is already there)
        _go(arm, gripper=GRIP_OPEN_DEG)
        _go(arm, shoulder=PICK_SHOULDER)     # reach down to the cube
        _go(arm, elbow=PICK_ELBOW)
        _go(arm, gripper=GRIP_HOLD_DEG)      # grab
        time.sleep(GRAB_SETTLE_SEC)          # let the grip settle before lifting
        _go(arm, elbow=LIFT_ELBOW)           # lift
        _to_drop_pose(arm, slot)
        _go(arm, gripper=GRIP_OPEN_DEG)      # release
        _home_from_drop(arm)
    except KeyboardInterrupt:
        print("\nStopped by user -- the arm is holding its last position. "
              "Switch off the servo power if you will not use it now.")
        raise
    return slot


def aim_at_slot(slot, arm=None):
    """
    Carry-pose check WITHOUT the pick: gripper closed as if holding a cube, go to the slot's
    drop pose, wait for Enter (compare with the slot mark), then release and return home.
    The pick itself is only in place_from_handoff (--place).
    """
    if arm is None:
        from arm_client import ArmClient
        arm = fixed_sorter.SlowArm(ArmClient())
    _go(arm, **HOME)
    _go(arm, gripper=GRIP_HOLD_DEG)          # closed, as if carrying (empty jaws just close fully)
    _to_drop_pose(arm, slot)
    input(f"At slot {slot} drop pose (base {SLOT_BASE_DEG[slot]}), gripper closed. Check it against "
          f"the mark, then press Enter to release and return home... ")
    _go(arm, gripper=GRIP_OPEN_DEG)
    _home_from_drop(arm)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--aim", type=int, metavar="SLOT")
    ap.add_argument("--place", type=int, metavar="SLOT")
    ap.add_argument("--home", action="store_true")
    a = ap.parse_args()

    if a.show:
        print("HOME:", HOME)
        print("Pick (handoff):", dict(base=HOME["base"], shoulder=PICK_SHOULDER, elbow=PICK_ELBOW))
        for s in range(N_SLOTS):
            print(f"Drop slot {s}: base {SLOT_BASE_DEG[s]}, shoulder {RAISED_SHOULDER}, elbow {DROP_ELBOW}")
        print(f"Gripper open {GRIP_OPEN_DEG}, hold {GRIP_HOLD_DEG}")
    elif a.home:
        print("Returns the arm to HOME: raise, rotate back, open the gripper (drops a held cube), lower.")
        if input("Type YES to move the arm: ").strip() == "YES":
            recover_home()
            print("Done.")
    elif a.aim is not None or a.place is not None:
        slot = a.aim if a.aim is not None else a.place
        if not 0 <= slot < N_SLOTS:
            raise SystemExit(f"slot must be 0..{N_SLOTS - 1}")
        print("Make sure the arm is at the HOME pose (or servo power is off while you place it there).")
        print(f"Base for slot {slot} = {SLOT_BASE_DEG[slot]}. Ctrl+C stops within one step.")
        if input("Type YES to move the arm: ").strip() == "YES":
            (aim_at_slot if a.aim is not None else place_from_handoff)(slot)
            print("Done.")
    else:
        ap.print_help()
