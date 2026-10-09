# SortBench — Live Camera + Arm Integration

## What's new

- `config.py` — arm geometry constants (link lengths, angle offsets). Placeholders until you measure the real arm.
- `settings.py` — phone IP, arm IP, and mode toggles, persisted to `settings.json`. Editable live from the dashboard's Settings panel — no code edits when your WiFi IP changes.
- `arm_ik.py` — inverse kinematics: (x, y, height in cm) → (base, shoulder, elbow) servo angles.
- `calibration.py` / `calibrate.py` — maps a pixel in the overhead photo to real-world cm relative to the arm's base, using a homography. Returns pixels unchanged until you run `calibrate.py`.
- `arm_client.py` — talks to the ESP32 (`/api/move_all`, `/api/status`), and sequences a full pick-and-place per cube.
- `test_arm_angles.py` — CLI jog tool for tuning servo offsets/directions once the arm exists.
- `app.py` — now pulls live frames from your phone, exposes `/api/settings` and `/api/arm_status`, and `/api/execute` does either a dry run or a real move depending on the `simulate_arm` setting.
- `templates/index.html` — Settings panel, live camera view, arm status readout.

Install one new dependency: `pip install requests` (everything else you already had).

## Part 1 — Live camera, right now (no arm needed)

1. On the phone, install **IP Webcam** (Android, free, on the Play Store).
2. Mount the phone overhead, open IP Webcam, hit **Start server**. It'll show something like `http://192.168.1.42:8080`.
3. Make sure the phone and laptop are on the **same WiFi network**.
4. Run the dashboard (`python app.py`), open it in your browser, and in the **Settings** panel:
   - Phone IP: `192.168.1.42` (whatever IP Webcam showed you, without `http://` or the port)
   - Phone Port: `8080`
   - Check **"Use live camera"**
   - Click **Save Settings**
5. Click **Show Live View** — you should see the tray, live, updating continuously.
6. Click **Scan** — this now grabs one fresh frame from the phone instead of using a static test image, then runs detection + digit prediction exactly as before.
7. **Plan Sort** and **Execute** work as before. With `simulate_arm` checked (the default), Execute is a dry run — it computes and shows the move sequence without touching any hardware, since there's no arm yet.

Nothing here needs the arm. You can fully demo detection + live view + sort planning today.

## Part 2 — Once the physical arm exists

1. **Flash `arm_controller.ino`** to the ESP32, filling in your real `WIFI_SSID`/`WIFI_PASSWORD`. Open the Serial Monitor at 115200 baud — it'll print the IP address once connected. That's your Arm IP.
2. In the dashboard **Settings** panel, enter that Arm IP and Save.
3. Click **Refresh Arm Status** — if it shows the 4 joint angles, the connection works.
4. **Measure the arm**: shoulder→elbow length, elbow→gripper length (cm), and how high the shoulder pivot sits above the table. Put these into `config.py` (`LINK_SHOULDER_ELBOW_CM`, `LINK_ELBOW_GRIPPER_CM`, `BASE_HEIGHT_CM`).
5. **Tune direction/offsets**: run `python test_arm_angles.py` and try commands like `base 90`, `shoulder 45`, `elbow 120`. Watch which way each joint actually moves, and adjust `BASE_OFFSET_DEG`/`BASE_DIR` (and the shoulder/elbow equivalents) in `config.py` until you're confident the math in `arm_ik.py` will point the arm the right way. This is the step most worth taking slowly — get it right once and everything downstream just works.
6. **Calibrate the camera**: take an overhead photo with the arm in place, then run:
   ```
   python calibrate.py path/to/that_photo.png
   ```
   Click at least 4 points you can also measure by hand on the real table (e.g. taped marks, or corners of a mat), and type in each one's real-world (x, y) in cm relative to the arm's base. This saves `calibration.json`; `pixel_to_arm()` will use it automatically from then on.
7. In Settings, **uncheck "Simulate arm"** and Save.
8. Click **Execute** — it will now actually convert each cube's pixel position through calibration + inverse kinematics and send real moves to the ESP32, one pick-and-place at a time.

If a move ever comes back "out of reach" or an angle outside 0-180°, that's `arm_ik.py` protecting you from telling a servo to do something impossible — it almost always means `config.py`'s link lengths/offsets need adjusting, not that the code is wrong.

## Notes

- Left/right physical layout: `sorter.py`'s `DEST_ORIGIN = (700, 50)` places sorted cubes toward the right side of the photo frame — keep your unsorted cubes on the left and this lines up with what you described. Adjust `DEST_ORIGIN`/`DEST_SPACING` in `sorter.py` if your frame size or slot spacing differs.
- `/api/execute` stops after the first hardware error rather than continuing blind — check the dashboard's error message, fix, then re-run Plan + Execute.
- The Manual Upload button is still there for testing without the live camera at all (e.g. debugging with your `test4.png`/`test5.png` photos).
