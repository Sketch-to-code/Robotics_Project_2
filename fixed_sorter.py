"""
fixed_sorter.py
---------------
Fixed-layout sorter: the camera only READS the digits; the arm goes to
pre-defined spots on the sheet computed from the layout numbers below
(no camera-to-arm calibration needed).

Layout (top view, arm pivot at the origin, x = forward, y = sideways).
All spots sit on two ARCS centred on the arm pivot, so every pick spot is the
same distance from the arm (this arm's reach is limited by distance):

    pick arc  (radius PICK_RADIUS_CM)                    [pick 0] [pick 1] [pick 2]
    drop arc  (radius PICK_RADIUS_CM - DROP_SETBACK_CM)  [drop 0] [drop 1] [drop 2]

Pick slot i and drop slot i share the same angle as seen from the arm.
The smallest digit goes to drop slot 0, the next to slot 1, and so on.

Usage (from the dashboard folder, venv active):
    python fixed_sorter.py --check          # is every spot reachable? which pick radius works?
    python fixed_sorter.py --plan 2 9 3     # dry run: print the move order, send nothing
    python fixed_sorter.py --run 2 9 3      # really move the arm
Digits are given in pick-slot order (slot 0 first).
"""

import argparse
import math
import time

import config
from arm_ik import solve, UnreachableError

# ---------------------------------------------------------------------
# LAYOUT -- edit these to match what you drew (all cm / degrees)
# ---------------------------------------------------------------------
PICK_RADIUS_CM = 10.5    # base rotation axis -> centre pick cube
ANGLE_STEP_DEG = 28.0    # angle between neighbouring slots as seen from the arm
DROP_SETBACK_CM = 3.0    # how much closer to the arm the drop arc is
N_SLOTS = 3

GRASP_Z_CM = 1.5         # gripper height when grabbing / releasing (about half a cube)
LIFT_Z_CM = 2.0          # gripper height while carrying (must be reachable -- see --check)

# ---------------------------------------------------------------------
# MOTOR SAFETY -- every move is split into small joint-space steps
# ---------------------------------------------------------------------
STEP_DEG = 1.0           # max change per joint per command (smaller = smoother/slower)
STEP_PAUSE_SEC = 0.05    # pause between steps -> roughly 15-20 deg/s per joint incl. HTTP time
GRIP_DEG = 80            # gripper angle while HOLDING a cube. Do NOT use the fully-closed value:
                         # jaws stopped by the cube while the servo pushes to 75 = stalled motor.
                         # Tune: the angle where the jaws just touch the cube, minus 2-3 deg.

# Optional raw servo angles (base, shoulder, elbow) to park the arm out of the
# camera view after sorting. None = don't park.
PARK_ANGLES = None


def _slot_xy(radius, i):
    ang = math.radians((i - (N_SLOTS - 1) / 2) * ANGLE_STEP_DEG)
    return (radius * math.cos(ang), radius * math.sin(ang))


def pick_xy(i):
    return _slot_xy(PICK_RADIUS_CM, i)


def drop_xy(i):
    return _slot_xy(PICK_RADIUS_CM - DROP_SETBACK_CM, i)


def all_poses():
    """Every pose the sequence needs: (label, xy, z)."""
    out = []
    for i in range(N_SLOTS):
        for kind, xy in (("pick", pick_xy(i)), ("drop", drop_xy(i))):
            out.append((f"{kind} {i} grasp", xy, GRASP_Z_CM))
            out.append((f"{kind} {i} lift", xy, LIFT_Z_CM))
    return out


def check_layout(verbose=True):
    ok_all = True
    for label, xy, z in all_poses():
        try:
            angles = solve(xy[0], xy[1], z)
            if verbose:
                print(f"OK    {label:14s} forward={xy[0]:5.1f} side={xy[1]:5.1f} z={z:3.1f} "
                      f"-> base/shoulder/elbow {angles}")
        except UnreachableError:
            ok_all = False
            if verbose:
                print(f"FAIL  {label:14s} forward={xy[0]:5.1f} side={xy[1]:5.1f} z={z:3.1f}")
    return ok_all


def find_pick_radius_window():
    """Scan PICK_RADIUS_CM; return the values for which every pose is reachable."""
    global PICK_RADIUS_CM
    saved, good = PICK_RADIUS_CM, []
    for tenth in range(60, 181):
        PICK_RADIUS_CM = tenth / 10
        if check_layout(verbose=False):
            good.append(PICK_RADIUS_CM)
    PICK_RADIUS_CM = saved
    return good


# ---------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------
def plan_moves(digits):
    """
    digits: digit read at each pick slot, slot 0 first.
    Returns [(pick_slot, drop_slot), ...]: smallest digit -> drop slot 0, etc.
    Move order is greedy nearest-neighbour: the next cube is the one whose
    pick spot is closest to where the gripper just dropped.
    """
    if len(digits) != N_SLOTS:
        raise ValueError(f"Expected {N_SLOTS} digits, got {len(digits)}")
    ranked = sorted(range(N_SLOTS), key=lambda s: (digits[s], s))
    target = {pick: drop for drop, pick in enumerate(ranked)}
    remaining, pos, moves = set(range(N_SLOTS)), drop_xy(N_SLOTS // 2), []
    while remaining:
        nxt = min(remaining, key=lambda s: math.dist(pos, pick_xy(s)))
        moves.append((nxt, target[nxt]))
        pos = drop_xy(target[nxt])
        remaining.remove(nxt)
    return moves


def travel_cm(moves):
    pos, total = drop_xy(N_SLOTS // 2), 0.0
    for p, d in moves:
        total += math.dist(pos, pick_xy(p)) + math.dist(pick_xy(p), drop_xy(d))
        pos = drop_xy(d)
    return total


# ---------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------
JOINTS = ("base", "shoulder", "elbow", "gripper")


class SlowArm:
    """
    Wraps ArmClient so every move is sent as many small steps (STEP_DEG each).
    The arm never lunges, and Ctrl+C stops it within one step, holding wherever
    it got to. ArmClient.move_all still applies the hard safe-range clamps.
    """

    def __init__(self, client):
        self.client = client
        self.cur = self._read_start()

    def _read_start(self):
        """Try to read the current angles from the ESP32; None if the format is unknown."""
        try:
            st = self.client.status()
        except Exception:
            return None
        if not isinstance(st, dict):
            return None
        for src in [st] + [v for v in st.values() if isinstance(v, dict)]:
            if all(j in src and isinstance(src[j], (int, float)) for j in JOINTS):
                cur = {j: float(src[j]) for j in JOINTS}
                # After a fresh ESP32 boot /api/status reports ASSUMED 90/90/90/90, not the real
                # pose (an elbow of 90 is outside its safe range). Don't trust values like that.
                if not (config.SHOULDER_MIN_SAFE_DEG <= cur["shoulder"] <= config.SHOULDER_MAX_SAFE_DEG
                        and config.ELBOW_MIN_SAFE_DEG <= cur["elbow"] <= config.ELBOW_MAX_SAFE_DEG
                        and config.GRIPPER_MIN_SAFE_DEG <= cur["gripper"] <= config.GRIPPER_MAX_SAFE_DEG):
                    return None
                return cur
        return None

    def _goto(self, target):
        # The ESP32 firmware reads angles with String.toInt(), which truncates "77.8" to 77,
        # so we round to whole degrees here and send integers.
        target = {j: float(round(v)) for j, v in target.items() if v is not None}
        if self.cur is None:
            # Start pose unknown: send this first target as-is (the firmware still smooths it).
            self.client.move_all(**{j: int(round(v)) for j, v in target.items()})
            self.cur = {j: target.get(j) for j in JOINTS}
            time.sleep(config.MOVE_SETTLE_SEC)
            return
        start = {j: self.cur[j] for j in target if self.cur.get(j) is not None}
        n = 1
        for j, v in start.items():
            n = max(n, math.ceil(abs(target[j] - v) / STEP_DEG))
        for k in range(1, n + 1):
            frac = k / n
            step = {j: int(round(start[j] + (target[j] - start[j]) * frac)) if j in start
                    else int(round(target[j])) for j in target}
            self.client.move_all(**step)
            time.sleep(STEP_PAUSE_SEC)
        for j, v in target.items():
            self.cur[j] = v
        time.sleep(0.2)

    def move_to(self, xy, z, gripper=None):
        base, shoulder, elbow = solve(xy[0], xy[1], z)
        self._goto({"base": base, "shoulder": shoulder, "elbow": elbow, "gripper": gripper})

    def grip(self, deg):
        self._goto({"gripper": deg})

    def raw(self, base, shoulder, elbow):
        self._goto({"base": base, "shoulder": shoulder, "elbow": elbow})


def run_sort(digits, arm=None):
    """Sort the cubes. digits = digit read at each pick slot, slot 0 first."""
    if not check_layout(verbose=False):
        raise UnreachableError("Layout has unreachable spots -- run: python fixed_sorter.py --check")
    moves = plan_moves(digits)
    if arm is None:
        from arm_client import ArmClient
        arm = SlowArm(ArmClient())

    try:
        for p, d in moves:
            pxy, dxy = pick_xy(p), drop_xy(d)
            arm.move_to(pxy, LIFT_Z_CM, gripper=config.GRIPPER_OPEN_DEG)
            arm.move_to(pxy, GRASP_Z_CM)
            arm.grip(GRIP_DEG)
            arm.move_to(pxy, LIFT_Z_CM)
            arm.move_to(dxy, LIFT_Z_CM)
            arm.move_to(dxy, GRASP_Z_CM)
            arm.grip(config.GRIPPER_OPEN_DEG)
            arm.move_to(dxy, LIFT_Z_CM)
        if PARK_ANGLES:
            arm.raw(*PARK_ANGLES)
    except KeyboardInterrupt:
        print("\nStopped by user -- the arm is holding its last position. "
              "Switch off the servo power if you will not use it now.")
    return moves


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--plan", nargs="+", type=int, metavar="DIGIT")
    ap.add_argument("--run", nargs="+", type=int, metavar="DIGIT")
    a = ap.parse_args()

    if a.check:
        print(f"BASE_HEIGHT_CM={config.BASE_HEIGHT_CM}  pick radius={PICK_RADIUS_CM}  "
              f"angle step={ANGLE_STEP_DEG}  setback={DROP_SETBACK_CM}  "
              f"grasp z={GRASP_Z_CM}  lift z={LIFT_Z_CM}\n")
        ok = check_layout()
        print("\nALL SPOTS REACHABLE" if ok else "\nSOME SPOTS UNREACHABLE")
        w = find_pick_radius_window()
        if w:
            print(f"Pick radius that works with these settings: {w[0]} to {w[-1]} cm (aim for the middle)")
        else:
            print("No pick radius makes every spot reachable with these settings. "
                  "Try a smaller DROP_SETBACK_CM or LIFT_Z_CM, or check BASE_HEIGHT_CM.")
    elif a.plan:
        m = plan_moves(a.plan)
        print("Moves (pick slot -> drop slot):", m, f"| travel ~{travel_cm(m):.1f} cm")
    elif a.run:
        print("Planned moves:", plan_moves(a.run))
        print(f"Gripper hold angle = {GRIP_DEG} (tune it to your cube!). Ctrl+C stops the arm within one step.")
        if input("Type YES to move the arm: ").strip() == "YES":
            print("Done:", run_sort(a.run))
        else:
            print("Cancelled.")
    else:
        ap.print_help()
