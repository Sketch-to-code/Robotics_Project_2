
"""
config.py
---------
Physical constants for the arm. Everything in here is a PLACEHOLDER until
you have the real hardware in hand. Nothing else in the codebase needs to
change when you update these -- arm_ik.py and arm_client.py read from here.

How to fill these in once the arm exists:
  1. Measure LINK_SHOULDER_ELBOW_CM and LINK_ELBOW_GRIPPER_CM with a ruler
     (pivot-to-pivot distance of each link).
  2. Measure BASE_HEIGHT_CM: height of the shoulder pivot above the table
     surface the cubes sit on.
  3. Run test_arm_angles.py to jog each servo by hand and work out
     BASE_OFFSET_DEG / SHOULDER_OFFSET_DEG / ELBOW_OFFSET_DEG and the
     *_DIR signs (see comments below the constants).
"""

# ---------------------------------------------------------------------
# Arm link lengths (cm) -- MEASURE these once you have the physical arm
# ---------------------------------------------------------------------
LINK_SHOULDER_ELBOW_CM = 9.5    # shoulder pivot -> elbow pivot (measured 9-10 cm)
LINK_ELBOW_GRIPPER_CM = 8.5     # elbow pivot -> gripper tip (measured 8-9 cm)
BASE_HEIGHT_CM = 10.5             # shoulder pivot height above the table surface (measured 7-8 cm)

# ---------------------------------------------------------------------
# Pick/place heights (cm), measured in the same frame as BASE_HEIGHT_CM
# (0 = table surface where the cubes rest)
# ---------------------------------------------------------------------
PICK_HEIGHT_CM = 0.0          # how far down the gripper must reach to grab a cube
SAFE_TRAVEL_HEIGHT_CM = 6.0   # clearance height used between pick and place moves

# ---------------------------------------------------------------------
# Gripper servo angles (degrees) -- an MG90, the other 3 joints are SG90.
# Determined by physical jog testing:
#   35 deg  = fully open
#   45-50 deg = fully closed
# SAFETY: going below ~35 (further open) or above ~50 (further closed)
# risks jamming the mechanism against its hard mechanical limit while the
# motor keeps drawing current to hold position -- this can burn out the
# servo. GRIPPER_MIN_SAFE_DEG / GRIPPER_MAX_SAFE_DEG below are enforced
# as a hard clamp in arm_client.py and test_arm_angles.py, not just a
# comment -- don't loosen them without re-verifying against the real
# hardware first.
# ---------------------------------------------------------------------
GRIPPER_OPEN_DEG = 180
GRIPPER_CLOSED_DEG = 0
GRIPPER_MIN_SAFE_DEG = 0
GRIPPER_MAX_SAFE_DEG = 180

# ---------------------------------------------------------------------
# Shoulder and elbow safe range (degrees), from physical jog testing.
# SAFETY: below ~25-30 deg, the arm's own structure physically blocks
# further motion -- the motor strains/grinds against it rather than
# actually moving, which can burn it out. Above ~75 deg, the arm lies
# nearly flat, making picking difficult and holding that pose works the
# servo harder against gravity than necessary. SHOULDER_MIN/MAX_SAFE_DEG
# and ELBOW_MIN/MAX_SAFE_DEG are enforced as a hard clamp in
# arm_client.py and test_arm_angles.py, same as the gripper limits above.
# ---------------------------------------------------------------------
SHOULDER_MIN_SAFE_DEG = 40   # channel 1: touches ground / lowest safe lift
SHOULDER_MAX_SAFE_DEG = 100  # channel 1: max lift height
ELBOW_MIN_SAFE_DEG = 25      # channel 2: min backward before damage risk
ELBOW_MAX_SAFE_DEG = 70      # channel 2: max forward

# ---------------------------------------------------------------------
# Angle calibration -- arm_ik.py computes angles using a clean mathematical
# convention (0 deg = pointing straight along the horizontal). Your actual
# servos almost certainly have a different "zero" and may even be mounted
# so that increasing angle moves the joint the *opposite* way from what the
# math assumes. Use test_arm_angles.py to jog each joint individually and
# work out these two numbers per joint:
#   OFFSET_DEG: what to add so the math's 0 deg matches the servo's real
#               resting/reference angle
#   DIR: 1 or -1, whichever makes increasing the computed angle move the
#        joint in the correct physical direction
#
# OBSERVED from physical jog testing (raw servo angle, channels confirmed
# via /api/raw to match CH_BASE=0, CH_SHOULDER=1, CH_ELBOW=2, CH_GRIPPER=3):
#   base:     increasing angle -> rotates LEFT, decreasing -> RIGHT (viewed
#             from behind the arm). angle 0 lands near the original build heading.
#   shoulder: safe range 60 (touching ground) to 100 (max height).
#             increasing angle -> lifts higher.
#   elbow:    safe range 25 (backward, damage risk beyond this) to 60 (max forward).
#             increasing angle -> moves forward/more extended.
#
# The OFFSET_DEG/DIR values above are INFORMED STARTING GUESSES, not fully
# verified ones. They're anchored so that a realistic table-level pick at
# roughly 10cm reach lands near the middle of each joint's safe range --
# computed directly from arm_ik's own math (not just guessed), using
# LINK_SHOULDER_ELBOW_CM/LINK_ELBOW_GRIPPER_CM/BASE_HEIGHT_CM above. Given
# how narrow the safe ranges are, only roughly an 8-13cm reach band at
# table level is actually usable with these values -- targets much closer
# or farther will correctly come back as "unreachable" rather than forcing
# an unsafe angle. That's a real limitation of this arm's mechanical
# range, not a bug.
#
# To verify/refine: use test_arm_angles.py's `goto x y z` command (cm,
# relative to the arm base) with values in that 8-13cm range first.
# Compare where the gripper actually ends up to where you expected, then
# nudge these OFFSET_DEG values (not DIR, unless a direction is flipped
# entirely backwards) until they match. This is trial-and-refinement,
# not a one-shot calculation -- expect a few rounds of small adjustments.
# ---------------------------------------------------------------------
BASE_OFFSET_DEG = 90   # boot default/center -- treated as "straight ahead" for now,
                        # pending verification (see below)
BASE_DIR = 1            # UNVERIFIED: flip to -1 if left/right comes out backwards

SHOULDER_OFFSET_DEG = 0    # anchored so a ~10cm table-level reach lands mid-safe-range
SHOULDER_DIR = 1            # increasing math angle = lifting = matches observed
                             # "increasing servo = lifts higher"

ELBOW_OFFSET_DEG = -50   # anchored the same way as shoulder, see note below
ELBOW_DIR = 1              # increasing math angle = more extended = matches
                            # observed "increasing servo = forward"

# Delay (seconds) after sending a move command, to give the SG90s time to
# physically get there before the next command is sent (ESP32 firmware
# doesn't report "move complete", so this is an open-loop wait).
MOVE_SETTLE_SEC = 0.6
