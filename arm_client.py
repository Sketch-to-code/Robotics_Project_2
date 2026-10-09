"""
arm_client.py
-------------
Talks to the ESP32 arm controller over a WIRED USB-serial link (pyserial)
and sequences a full pick-and-place using arm_ik.solve() for each joint move.

Firmware: arm_controller_serial.ino. Protocol (115200 baud, one line each):
    P            -> {"pong":true}
    S            -> {"base":..,"shoulder":..,"elbow":..,"gripper":..}
    M b s e g    -> moves (replies only when the arm has arrived), same reply as S.
                    -1 = leave that joint where it is.
Anything the board prints that is not a JSON line (boot noise, '#' debug
lines) is ignored.

Settings (settings.json, see settings.py):
    arm_serial_port   "" = auto-detect the ESP32, or e.g. "COM5"
    arm_baud          115200

This module is the only place that talks to the arm -- app.py never does.
The public interface (ArmClient.move_all / status / move_to / pick_and_place,
ArmConnectionError) is unchanged from the old WiFi version.

Quick check from the dashboard folder:   python arm_client.py
"""

import json
import threading
import time

import serial
from serial.tools import list_ports

import config
import settings as cfg
from arm_ik import solve as ik_solve


MAX_ATTEMPTS = 3              # a move is an ABSOLUTE target, so re-sending it is harmless
RETRY_PAUSE_SEC = 0.4
CONNECT_TIMEOUT_SEC = 6       # how long to wait for the board to answer after the port opens
MOVE_TIMEOUT_SEC = 20         # one M command blocks until the arm has arrived
QUICK_TIMEOUT_SEC = 3         # status / ping

# USB-serial chips used on ESP32 boards: CP210x, CH340/CH341, FTDI, Espressif native USB.
_ESP_VIDS = {0x10C4, 0x1A86, 0x0403, 0x303A}


class ArmConnectionError(Exception):
    pass


def _clamp_joint(joint_name, angle, min_safe, max_safe):
    """
    Hard safety clamp based on physical testing: several joints on this
    arm can grind against the arm's own structure or strain the motor if
    commanded outside their verified safe range. This applies no matter
    where the angle came from (config constants, a jog command, a future
    planner bug) -- clamping happens here, in the one place all commands
    funnel through.
    """
    if angle is None:
        return None
    clamped = max(min_safe, min(max_safe, angle))
    if clamped != angle:
        print(f"WARNING: {joint_name} angle {angle} is outside the safe range "
              f"({min_safe}-{max_safe}); clamped to {clamped} to avoid damaging the arm.")
    return clamped


def _angle_arg(angle):
    """None -> -1 (the firmware's 'leave this joint'); otherwise a whole degree in 0..180."""
    if angle is None:
        return -1
    return int(round(max(0, min(180, angle))))


class ArmClient:
    def __init__(self):
        # The port is opened lazily on the first command, NOT here. Flask's debug reloader imports
        # app.py twice; opening the port at import time would make the second copy fail "port busy".
        self._ser = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ connection
    def _port_name(self):
        wanted = (cfg.get_setting("arm_serial_port") or "").strip()
        if wanted:
            return wanted
        ports = list(list_ports.comports())
        candidates = [p for p in ports if p.vid in _ESP_VIDS]
        seen = ", ".join(f"{p.device} ({p.description})" for p in ports) or "none"
        if len(candidates) == 1:
            return candidates[0].device
        if not candidates:
            raise ArmConnectionError(
                f"No ESP32 USB-serial port found. Plug in the arm's USB cable, or set "
                f"\"arm_serial_port\" in settings.json (e.g. \"COM5\"). Ports seen: {seen}.")
        raise ArmConnectionError(
            f"More than one possible ESP32 port found ({seen}). Set \"arm_serial_port\" in "
            f"settings.json to the right one (e.g. \"COM5\").")

    def _open(self):
        if self._ser is not None and self._ser.is_open:
            return self._ser
        port = self._port_name()
        baud = int(cfg.get_setting("arm_baud", 115200) or 115200)

        ser = serial.Serial()
        ser.port, ser.baudrate = port, baud
        ser.timeout = 0.5
        ser.write_timeout = 2
        # On most ESP32 boards DTR/RTS drive the EN/BOOT pins, so toggling them on open resets the
        # chip. Keeping both low avoids that where the driver allows it; _wait_ready() copes if not.
        ser.dtr = False
        ser.rts = False
        try:
            ser.open()
        except (serial.SerialException, OSError) as e:
            raise ArmConnectionError(
                f"Could not open {port}: {e}. Is the Arduino Serial Monitor (or another program) "
                f"still using it? Is the cable plugged in?") from e
        self._ser = ser
        try:
            self._wait_ready(ser, port)
        except Exception:
            self.close()
            raise
        return ser

    def _wait_ready(self, ser, port):
        """Ping until the board answers (it may be rebooting because the port was just opened)."""
        deadline = time.time() + CONNECT_TIMEOUT_SEC
        while time.time() < deadline:
            try:
                ser.write(b"P\n")
                ser.flush()
                end = time.time() + 0.8
                while time.time() < end:
                    text = ser.readline().decode("ascii", errors="ignore").strip()
                    if text.startswith("{") and ("pong" in text or "ready" in text):
                        time.sleep(0.1)
                        ser.reset_input_buffer()
                        return
            except (serial.SerialException, OSError) as e:
                raise ArmConnectionError(f"Lost the connection to {port} while starting up: {e}") from e
        raise ArmConnectionError(
            f"The board on {port} did not answer. Wrong port? Is arm_controller_serial.ino flashed "
            f"(not the old WiFi sketch), and is the baud rate 115200?")

    def close(self):
        with self._lock:
            if self._ser is not None:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None

    def _command(self, line, timeout, want_key="base"):
        """Send one command line, return the board's JSON reply (retrying dropped links)."""
        last = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            with self._lock:
                try:
                    ser = self._open()          # ArmConnectionError from here is not retried
                    ser.reset_input_buffer()
                    ser.write((line + "\n").encode("ascii"))
                    ser.flush()
                    deadline = time.time() + timeout
                    while time.time() < deadline:
                        raw = ser.readline()
                        if not raw:
                            continue
                        text = raw.decode("ascii", errors="ignore").strip()
                        if not text.startswith("{"):
                            continue            # boot noise / '#' debug line
                        try:
                            reply = json.loads(text)
                        except ValueError:
                            continue
                        if "error" in reply:    # a protocol error: retrying will not help
                            raise ArmConnectionError(f"The arm rejected '{line}': {reply['error']}")
                        if want_key in reply:
                            return reply        # (other JSON such as a late pong is skipped)
                    last = TimeoutError(f"no reply within {timeout}s")
                except (serial.SerialException, OSError) as e:   # includes an unplugged cable
                    last = e
                self.close()                    # reconnect on the next attempt
            print(f"WARNING: arm command '{line}' failed (attempt {attempt}/{MAX_ATTEMPTS}): {last}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_PAUSE_SEC)
        raise ArmConnectionError(
            f"Lost contact with the arm after {MAX_ATTEMPTS} attempts ({last}). Check the USB cable "
            f"and that nothing else is using the port.") from last

    # ------------------------------------------------------------------ public API
    def move_all(self, base=None, shoulder=None, elbow=None, gripper=None):
        shoulder = _clamp_joint("shoulder", shoulder, config.SHOULDER_MIN_SAFE_DEG, config.SHOULDER_MAX_SAFE_DEG)
        elbow = _clamp_joint("elbow", elbow, config.ELBOW_MIN_SAFE_DEG, config.ELBOW_MAX_SAFE_DEG)
        gripper = _clamp_joint("gripper", gripper, config.GRIPPER_MIN_SAFE_DEG, config.GRIPPER_MAX_SAFE_DEG)
        args = " ".join(str(_angle_arg(v)) for v in (base, shoulder, elbow, gripper))
        return self._command(f"M {args}", MOVE_TIMEOUT_SEC)

    def status(self):
        return self._command("S", QUICK_TIMEOUT_SEC)

    def move_to(self, xy_cm, z_cm, gripper=None):
        """Move the gripper to (xy_cm, z_cm) via inverse kinematics."""
        base_deg, shoulder_deg, elbow_deg = ik_solve(xy_cm[0], xy_cm[1], z_cm)
        self.move_all(base=base_deg, shoulder=shoulder_deg, elbow=elbow_deg, gripper=gripper)
        time.sleep(config.MOVE_SETTLE_SEC)

    def pick_and_place(self, pick_cm, place_cm):
        """
        Full pick-and-place sequence for one cube:
        approach above pick -> descend -> close gripper -> lift ->
        travel above place -> descend -> open gripper -> lift.
        """
        travel_z = config.SAFE_TRAVEL_HEIGHT_CM
        pick_z = config.PICK_HEIGHT_CM

        self.move_to(pick_cm, travel_z, gripper=config.GRIPPER_OPEN_DEG)
        self.move_to(pick_cm, pick_z)
        self.move_all(gripper=config.GRIPPER_CLOSED_DEG)
        time.sleep(config.MOVE_SETTLE_SEC)
        self.move_to(pick_cm, travel_z)

        self.move_to(place_cm, travel_z)
        self.move_to(place_cm, pick_z)
        self.move_all(gripper=config.GRIPPER_OPEN_DEG)
        time.sleep(config.MOVE_SETTLE_SEC)
        self.move_to(place_cm, travel_z)


if __name__ == "__main__":
    # Connection check. Does NOT move the arm (it only asks for the current angles).
    print("Serial ports on this computer:")
    for p in list_ports.comports():
        print(f"  {p.device:10s} {p.description}")
    print("\nAsking the arm for its status ...")
    try:
        print("Arm status:", ArmClient().status())
    except ArmConnectionError as e:
        print("FAILED:", e)
