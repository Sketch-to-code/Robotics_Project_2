import os
import threading
import time

from flask import Flask, render_template, jsonify, request, send_from_directory
import numpy as np
import requests
from tensorflow.keras.models import load_model
from PIL import Image

from detect_cubes import detect_cubes
from arm_client import ArmClient, ArmConnectionError
import config
import settings as cfg
import taught_sorter
from sort_modes import slot_targets, normalize_mode

app = Flask(__name__)

# Base directory = wherever app.py itself lives, regardless of cwd
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "..", "digit_model", "digit_cnn_model.keras")
TEST_IMAGES_DIR = os.path.join(BASE_DIR, "..", "digit_model", "test_images")
DETECTIONS_DIR = os.path.join(BASE_DIR, "detections")

# Ensure test images directory exists
os.makedirs(TEST_IMAGES_DIR, exist_ok=True)
os.makedirs(DETECTIONS_DIR, exist_ok=True)

# Filename used to save whatever frame was just pulled from the phone's
# live stream, so the rest of the pipeline works seamlessly.
LIVE_CAPTURE_FILENAME = "live_capture.png"

# Filename used for manually-uploaded images
UPLOAD_FILENAME = "uploaded_capture.png"

# Fallback image used only when /api/scan is called with no source/image
DEFAULT_IMAGE = "test1.png"

model = load_model(MODEL_PATH)
arm = ArmClient()

# In-memory cache of the last scan
_last_scan_cache = {"image_path": None, "cubes": None}


def predict_crop(crop_bgr):
    img = Image.fromarray(crop_bgr).convert("L").resize((64, 64))
    img_array = np.array(img) / 255.0
    img_array = img_array.reshape(1, 64, 64, 1)
    prediction = model.predict(img_array, verbose=0)
    digit = int(np.argmax(prediction))
    confidence = float(np.max(prediction))
    return digit, confidence


def grab_live_frame():
    phone_ip = cfg.get_setting("phone_ip")
    phone_port = cfg.get_setting("phone_port", 8080)
    if not phone_ip:
        raise RuntimeError(
            "No phone IP configured. Open Settings, start IP Webcam on your "
            "phone, and enter the IP address it shows."
        )

    snapshot_url = f"http://{phone_ip}:{phone_port}/shot.jpg"
    resp = requests.get(snapshot_url, timeout=6)
    resp.raise_for_status()

    save_path = os.path.join(TEST_IMAGES_DIR, LIVE_CAPTURE_FILENAME)

    rotation = int(cfg.get_setting("camera_rotation", 0) or 0)
    if rotation in (90, 180, 270):
        from PIL import Image as PILImage
        import io
        img = PILImage.open(io.BytesIO(resp.content))
        img = img.rotate(-rotation, expand=True)
        img.save(save_path)
    else:
        with open(save_path, "wb") as f:
            f.write(resp.content)

    return LIVE_CAPTURE_FILENAME


def run_scan(image_filename):
    image_path = os.path.join(TEST_IMAGES_DIR, image_filename)
    raw_cubes = detect_cubes(image_path, output_dir=DETECTIONS_DIR, debug=True)

    cubes = []
    for c in raw_cubes:
        digit, confidence = predict_crop(c["digit_crop"])
        cubes.append({
            "id": c["id"],
            "bbox": c["bbox"],
            "centroid_px": c["centroid_px"],
            "predicted_digit": digit,
            "confidence": round(confidence * 100, 2),
        })

    _last_scan_cache["image_path"] = image_path
    _last_scan_cache["cubes"] = cubes
    return cubes, image_path


@app.route('/')
def home():
    return render_template('index.html')


def _clean_roi(value):
    """Pick-area rectangle [x0, y0, x1, y1] in image pixels, or None to clear it."""
    if not value:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("handoff_roi must be 4 numbers: x0, y0, x1, y1")
    x0, y0, x1, y1 = (int(round(float(v))) for v in value)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("The pick area must have a width and a height")
    return [x0, y0, x1, y1]


@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or {}
        allowed = {"phone_ip", "phone_port", "arm_serial_port", "arm_baud",
                   "simulate_arm", "camera_rotation", "handoff_roi"}
        updates = {k: v for k, v in data.items() if k in allowed}
        if "handoff_roi" in updates:
            try:
                updates["handoff_roi"] = _clean_roi(updates["handoff_roi"])
            except (ValueError, TypeError) as e:
                return jsonify({"status": "error", "message": str(e)}), 400
        if "arm_serial_port" in updates or "arm_baud" in updates:
            arm.close()                      # reopen on the new port with the next command
        new_settings = cfg.update(updates)
        return jsonify({"status": "ok", "settings": new_settings})
    return jsonify({"status": "ok", "settings": cfg.get_all()})


@app.route('/api/scan')
def scan():
    """
    ?source=live    - pulls a fresh frame from the phone's live camera right now
    ?source=upload  - uses whatever was last uploaded via /api/upload
    ?image=filename - explicit override (dev/testing)
    """
    source = request.args.get("source")
    image_filename = request.args.get("image")

    try:
        if not image_filename:
            if source == "live":
                image_filename = grab_live_frame()
            elif source == "upload":
                image_filename = UPLOAD_FILENAME
            else:
                image_filename = DEFAULT_IMAGE
        cubes, image_path = run_scan(image_filename)
    except Exception as e:
        print(f"Scan error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 400

    result = {
        "status": "ok",
        "cubes_found": len(cubes),
        "cubes": cubes,
        "annotated_image_url": "/detections/debug_annotated.png",
        "original_image_url": f"/test_images/{os.path.basename(image_path)}",
    }
    return jsonify(result)


# ---------------------------------------------------------------------
# Handoff sorter: show the arrangement once, then hand cubes over one at a
# time; each is placed in its sorted slot.
#   feed = "manual": the digits were typed in the order the cubes will be handed
#                    over, so NEXT CUBE already knows which cube is on the pick spot.
#   feed = "camera": the camera reads the cube sitting on the pick spot each time.
# ---------------------------------------------------------------------
# Fallback pick area (x0, y0, x1, y1 in image pixels) if none is saved from the dashboard.
# None = accept any cube (only safe while just ONE cube is visible).
HANDOFF_ROI = None
MIN_CONFIDENCE = 80.0          # percent; below this the cube is not moved

_handoff = {"mode": None, "target": [], "filled": [], "queue": [], "feed": None}
_arm_lock = threading.Lock()
_slow_arm = None


def _get_slow_arm():
    global _slow_arm
    if _slow_arm is None:
        from fixed_sorter import SlowArm
        _slow_arm = SlowArm(arm)
    return _slow_arm


def _in_roi(cube):
    roi = cfg.get_setting("handoff_roi") or HANDOFF_ROI
    if not roi:
        return True
    x, y = cube["centroid_px"]
    x0, y0, x1, y1 = roi
    return x0 <= x <= x1 and y0 <= y <= y1


def _next_slot(digit):
    for i, d in enumerate(_handoff["target"]):
        if d == digit and not _handoff["filled"][i]:
            return i
    return None


def _handoff_state():
    queue = _handoff["queue"]
    return {"mode": _handoff["mode"], "target": _handoff["target"], "filled": _handoff["filled"],
            "queue": list(queue), "next_digit": queue[0] if queue else None, "feed": _handoff["feed"]}


@app.route('/api/handoff/scan')
def handoff_scan():
    """
    Camera mode: look at the tray and report the digits (left to right). Changes no state, so the
    digits can be checked and corrected before START.
      ?source=live (default) | upload
    """
    try:
        fname = UPLOAD_FILENAME if request.args.get("source") == "upload" else grab_live_frame()
        cubes, _ = run_scan(fname)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    low = [{"digit": c["predicted_digit"], "confidence": c["confidence"]}
           for c in cubes if c["confidence"] < MIN_CONFIDENCE]
    return jsonify({
        "status": "ok",
        "digits": [c["predicted_digit"] for c in cubes],
        "confidences": [c["confidence"] for c in cubes],
        "expected": taught_sorter.N_SLOTS,
        "count_ok": len(cubes) == taught_sorter.N_SLOTS,
        "low_confidence": low,
        "annotated_image_url": "/detections/debug_annotated.png",
    })


@app.route('/api/handoff/start')
def handoff_start():
    """
    Decide which digit goes in which slot.
      ?mode=ascending (default) | descending | evenodd
      ?digits=3,8,2     the digits (skips the camera)
      ?feed=manual      the digits are in hand-over order; NEXT CUBE then needs no camera
      ?feed=camera      NEXT CUBE reads each cube with the camera
      (no digits)       read the arrangement from the camera / ?source=upload
    """
    try:
        mode = normalize_mode(request.args.get("mode"))
        if request.args.get("digits"):
            digits = [int(d) for d in request.args["digits"].replace(" ", "").split(",") if d != ""]
            if any(d < 0 or d > 9 for d in digits):
                raise ValueError("Digits must be between 0 and 9.")
        else:
            fname = UPLOAD_FILENAME if request.args.get("source") == "upload" else grab_live_frame()
            cubes, _ = run_scan(fname)
            low = [(c["id"], c["predicted_digit"], c["confidence"]) for c in cubes
                   if c["confidence"] < MIN_CONFIDENCE]
            if low:
                raise ValueError(
                    f"Low-confidence read(s) (cube id, digit, %): {low}. Straighten the cubes in the "
                    f"middle of the frame and rescan, or type the digits yourself.")
            digits = [c["predicted_digit"] for c in cubes]
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    if len(digits) != taught_sorter.N_SLOTS:
        return jsonify({"status": "error",
                        "message": f"Expected {taught_sorter.N_SLOTS} cubes, got {digits}. "
                                   f"Fix the scene, or type the digits yourself."}), 400

    feed = request.args.get("feed", "")
    _handoff["mode"] = mode
    _handoff["target"] = slot_targets(digits, mode)
    _handoff["filled"] = [False] * taught_sorter.N_SLOTS
    _handoff["queue"] = list(digits) if feed == "manual" else []
    _handoff["feed"] = feed or None
    return jsonify({"status": "ok", "read": digits, **_handoff_state()})


@app.route('/api/handoff/next')
def handoff_next():
    """
    Put the cube on the pick spot into its sorted slot.
      ?digit=3    say which cube it is (overrides everything, also skips the camera)
    Otherwise: manual feed -> the next digit in the typed hand-over order; camera feed -> the camera reads it.
    """
    if not _handoff["target"]:
        return jsonify({"status": "error", "message": "Press START first."}), 400
    if all(_handoff["filled"]):
        return jsonify({"status": "done", "message": "All slots are full.", **_handoff_state()})
    if not _arm_lock.acquire(blocking=False):
        return jsonify({"status": "busy", "message": "The arm is still moving."}), 409

    try:
        if request.args.get("digit"):
            try:
                digit, conf = int(request.args["digit"]), 100.0
            except ValueError:
                return jsonify({"status": "error", "message": "digit must be a number 0-9"}), 400
        elif _handoff["queue"]:
            digit, conf = _handoff["queue"][0], 100.0
        else:
            reads = []
            for _ in range(2):                       # two frames must agree
                cubes, _ = run_scan(grab_live_frame())
                cands = [c for c in cubes if _in_roi(c)]
                if len(cands) != 1:
                    return jsonify({
                        "status": "error",
                        "message": f"Expected exactly 1 cube on the pick spot, saw {len(cands)}.",
                        "centroids": [c["centroid_px"] for c in cubes]}), 400
                reads.append(cands[0])
                time.sleep(0.3)
            if reads[0]["predicted_digit"] != reads[1]["predicted_digit"]:
                return jsonify({"status": "error",
                                "message": "Two frames read different digits - hold the cube still and retry.",
                                "reads": [r["predicted_digit"] for r in reads]}), 400
            digit = reads[0]["predicted_digit"]
            conf = min(r["confidence"] for r in reads)
            if conf < MIN_CONFIDENCE:
                return jsonify({"status": "error",
                                "message": f"Read {digit} but only {conf}% sure - not moving.",
                                "digit": digit}), 400

        slot = _next_slot(digit)
        if slot is None:
            return jsonify({"status": "error",
                            "message": f"Digit {digit} is not expected, or its slot is already full.",
                            **_handoff_state()}), 400

        simulate = cfg.get_setting("simulate_arm", True)
        if not simulate:
            try:
                taught_sorter.place_from_handoff(slot, _get_slow_arm())
            except (ArmConnectionError, ValueError) as e:
                return jsonify({"status": "error",
                                "message": f"{e}  The arm may be stopped mid-air: press HOME ARM before "
                                           f"trying again. This cube was NOT marked as placed."}), 400
        _handoff["filled"][slot] = True
        if digit in _handoff["queue"]:
            _handoff["queue"].remove(digit)
        return jsonify({"status": "simulated" if simulate else "placed",
                        "digit": digit, "confidence": conf, "slot": slot, **_handoff_state()})
    finally:
        _arm_lock.release()


@app.route('/api/handoff/reset')
def handoff_reset():
    global _slow_arm
    _handoff["mode"], _handoff["target"], _handoff["filled"] = None, [], []
    _handoff["queue"], _handoff["feed"] = [], None
    _slow_arm = None                 # re-read the arm's real pose the next time it is used
    return jsonify({"status": "ok", **_handoff_state()})


@app.route('/api/handoff/state')
def handoff_state():
    return jsonify({"status": "ok", "simulate": bool(cfg.get_setting("simulate_arm", True)),
                    **_handoff_state()})


@app.route('/api/handoff/home')
def handoff_home():
    """Bring the arm back to HOME after an error (raise -> rotate back -> open gripper -> lower)."""
    if cfg.get_setting("simulate_arm", True):
        return jsonify({"status": "simulated", "message": "Simulate mode is on -- the arm did not move."})
    if not _arm_lock.acquire(blocking=False):
        return jsonify({"status": "busy", "message": "The arm is still moving."}), 409
    try:
        taught_sorter.recover_home(_get_slow_arm())
        return jsonify({"status": "ok", "message": "Arm returned to HOME."})
    except (ArmConnectionError, ValueError) as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    finally:
        _arm_lock.release()


# ---------------------------------------------------------------------
# Manual arm control (jogging joints to find / check poses)
# ---------------------------------------------------------------------
def _joint_limits():
    return {"base": [0, 180],
            "shoulder": [config.SHOULDER_MIN_SAFE_DEG, config.SHOULDER_MAX_SAFE_DEG],
            "elbow": [config.ELBOW_MIN_SAFE_DEG, config.ELBOW_MAX_SAFE_DEG],
            "gripper": [config.GRIPPER_MIN_SAFE_DEG, config.GRIPPER_MAX_SAFE_DEG]}


@app.route('/api/arm/pose')
def arm_pose():
    simulate = bool(cfg.get_setting("simulate_arm", True))
    if simulate:
        return jsonify({"status": "simulated", "pose": None, "limits": _joint_limits(),
                        "message": "Simulate is on - there is no arm to read."})
    try:
        return jsonify({"status": "ok", "pose": arm.status(), "limits": _joint_limits()})
    except ArmConnectionError as e:
        return jsonify({"status": "error", "message": str(e), "limits": _joint_limits()}), 400


@app.route('/api/arm/move')
def arm_move():
    """Move chosen joints, e.g. ?base=56&shoulder=40 (any subset). Angles are limited to the safe ranges."""
    limits = _joint_limits()
    joints, clamped = {}, {}
    for name in ("base", "shoulder", "elbow", "gripper"):
        raw = request.args.get(name)
        if raw in (None, ""):
            continue
        try:
            wanted = float(raw)
        except ValueError:
            return jsonify({"status": "error", "message": f"{name} must be a number"}), 400
        lo, hi = limits[name]
        safe = max(lo, min(hi, wanted))
        if safe != wanted:
            clamped[name] = int(round(safe))
        joints[name] = int(round(safe))
    if not joints:
        return jsonify({"status": "error", "message": "Give at least one joint, e.g. base=56"}), 400

    if cfg.get_setting("simulate_arm", True):
        return jsonify({"status": "simulated", "moved": joints, "clamped": clamped,
                        "message": "Simulate is on - the arm did not move."})
    if not _arm_lock.acquire(blocking=False):
        return jsonify({"status": "busy", "message": "The arm is still moving."}), 409
    try:
        _get_slow_arm()._goto(joints)        # small steps; the first move of a joint after power-up jumps
        return jsonify({"status": "moved", "moved": joints, "clamped": clamped, "pose": arm.status()})
    except (ArmConnectionError, ValueError) as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    finally:
        _arm_lock.release()


@app.route('/api/arm_status')
def arm_status():
    try:
        status = arm.status()
        return jsonify({"status": "ok", "arm": status})
    except ArmConnectionError as e:
        return jsonify({"status": "error", "message": str(e)}), 400


# ---------------------------------------------------------------------
# Status chips + camera helpers
# ---------------------------------------------------------------------
@app.route('/api/health')
def health():
    """?camera=1 also checks that the phone camera answers."""
    simulate = bool(cfg.get_setting("simulate_arm", True))
    out = {"status": "ok", "simulate": simulate}
    if simulate:
        out["arm"] = {"state": "simulated"}
    else:
        try:
            out["arm"] = {"state": "connected", "pose": arm.status()}
        except ArmConnectionError as e:
            out["arm"] = {"state": "error", "message": str(e)}

    if request.args.get("camera") == "1":
        ip = cfg.get_setting("phone_ip")
        port = cfg.get_setting("phone_port", 8080)
        if not ip:
            out["camera"] = {"state": "unset", "message": "No phone IP set (Settings)."}
        else:
            try:
                r = requests.get(f"http://{ip}:{port}/shot.jpg", timeout=2, stream=True)
                ok = r.status_code == 200
                r.close()
                out["camera"] = {"state": "ready" if ok else "error",
                                 "message": "" if ok else f"The phone answered with HTTP {r.status_code}."}
            except requests.RequestException:
                out["camera"] = {"state": "error",
                                 "message": f"Cannot reach {ip}:{port}. Is IP Webcam running with the server started?"}
    return jsonify(out)


@app.route('/api/camera/snapshot')
def camera_snapshot():
    """Grab one live frame (used to draw the pick area on)."""
    try:
        fname = grab_live_frame()
        with Image.open(os.path.join(TEST_IMAGES_DIR, fname)) as im:
            width, height = im.size
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    return jsonify({"status": "ok", "image_url": f"/test_images/{fname}", "width": width, "height": height})


@app.route('/detections/<path:filename>')
def serve_detection(filename):
    return send_from_directory(DETECTIONS_DIR, filename)


@app.route('/test_images/<path:filename>')
def serve_test_image(filename):
    return send_from_directory(TEST_IMAGES_DIR, filename)


@app.route('/api/upload', methods=['POST'])
def upload_image():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No selected file"}), 400

    if file:
        save_path = os.path.join(TEST_IMAGES_DIR, UPLOAD_FILENAME)
        file.save(save_path)
        return jsonify({"status": "ok", "message": "Image uploaded successfully!"})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
