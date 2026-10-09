"""
settings.py
-----------
Small persisted key-value store for things that change often (phone IP,
arm serial port, whether to simulate) so you never have to edit Python source
just because your WiFi handed out a new IP address or the arm moved to a new
USB port. Backed by settings.json sitting next to this file; created
automatically on first write.
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")

DEFAULTS = {
    "phone_ip": "",          # e.g. "192.168.1.42" -- shown by the IP Webcam app
    "phone_port": 8080,      # IP Webcam's default port
    "arm_serial_port": "",   # "" = auto-detect the ESP32; or e.g. "COM5" (Windows), "/dev/ttyUSB0" (Linux)
    "arm_baud": 115200,      # must match SERIAL_BAUD in arm_controller_serial.ino
    "arm_ip": "",            # LEGACY (WiFi version) -- no longer used, kept so the old Settings panel still loads
    "arm_port": 80,          # LEGACY (WiFi version) -- no longer used
    "simulate_arm": True,      # False = actually send moves to the ESP32
    "camera_rotation": 0,      # 0, 90, 180, or 270 -- corrects a sideways/upside-down phone feed
}


def _load():
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH) as f:
                data = json.load(f)
            return {**DEFAULTS, **data}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULTS)


_settings = _load()


def get_setting(key, default=None):
    return _settings.get(key, default)


def get_all():
    return dict(_settings)


def update(new_values):
    """Merge new_values into the current settings and persist to disk."""
    _settings.update(new_values)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(_settings, f, indent=2)
    return dict(_settings)
