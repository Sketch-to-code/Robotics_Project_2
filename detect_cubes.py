import cv2
import numpy as np
import sys
import os


def contained_ratio(boxA, boxB):
    """Fraction of boxA's area that overlaps boxB."""
    xA1, yA1, wA, hA = boxA
    xB1, yB1, wB, hB = boxB
    xA2, yA2 = xA1 + wA, yA1 + hA
    xB2, yB2 = xB1 + wB, yB1 + hB

    ix1, iy1 = max(xA1, xB1), max(yA1, yB1)
    ix2, iy2 = min(xA2, xB2), min(yA2, yB2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih

    areaA = wA * hA
    return inter / areaA if areaA > 0 else 0


# ---------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------
# A pixel counts as "ink" if it is darker than INK_FRACTION * (typical background brightness).
# The digit is dark brown/red (about 20-45% of the background brightness); a cast shadow is only
# about 60-80%. Lower = stricter (ignores more shadow, but may lose a faint digit such as a red 9);
# raise it if a digit disappears from debug_threshold.png.
INK_FRACTION = 0.55
# The ink is maroon/red, a cast shadow is neutral gray. "Redness" = R - (G+B)/2 is about 40-170 on the
# digits and about 0-10 on shadows, so requiring it removes shadows even when they are as dark as the ink.
# The cream cube face is bright, so it fails the darkness test first. Set to 0 to switch the colour test
# off (e.g. if you ever use black ink).
REDNESS_MIN = 20
MIN_AREA = 300
MAX_AREA = 200000
MAX_AREA_FRACTION = 0.08        # a box bigger than 8% of the frame is a shadow/table, not a digit
EDGE_MARGIN = 3                 # boxes touching the frame edge are table edges / vignette, not digits
MIN_ASPECT, MAX_ASPECT = 0.15, 1.6   # width/height; a "1" is about 0.3
CONTAINMENT_THRESHOLD = 0.6     # drop a box if >60% of its area sits inside a bigger box


def detect_cubes(image_path, output_dir="detections", debug=True):
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Could not read image: {image_path}")

    img = cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")
    os.makedirs(output_dir, exist_ok=True)
    for old in os.listdir(output_dir):            # crops from earlier scans would be mistaken for this scan's
        if old.startswith("cube_") and old.endswith("_digit.png"):
            os.remove(os.path.join(output_dir, old))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape

    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # Threshold relative to the background instead of Otsu. Otsu splits the frame into "dark" and
    # "light" and pulls soft shadows and vignetting into the dark class, which is what merged the
    # cube shadows into the digit boxes. Assumes a light background (white paper / table).
    bg = float(np.median(blurred))
    ink_thresh = INK_FRACTION * bg
    _, thresh = cv2.threshold(blurred, ink_thresh, 255, cv2.THRESH_BINARY_INV)

    if REDNESS_MIN > 0:
        soft = cv2.GaussianBlur(img, (5, 5), 0).astype(np.int16)
        redness = soft[..., 2] - (soft[..., 1] + soft[..., 0]) // 2      # R - (G+B)/2
        thresh[redness < REDNESS_MIN] = 0                                # neutral gray = shadow, not ink

    kernel = np.ones((15, 15), np.uint8)
    clean = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
    clean = cv2.morphologyEx(clean, cv2.MORPH_OPEN, kernel, iterations=1)

    contours, _ = cv2.findContours(
        clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    max_area = min(MAX_AREA, MAX_AREA_FRACTION * W * H)

    # Pass 1: collect all valid boxes (size, edge and shape filtered, not yet dedup'd)
    raw_boxes = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < MIN_AREA or area > max_area:
            continue
        x, y, w, h = cv2.boundingRect(cnt)
        if x <= EDGE_MARGIN or y <= EDGE_MARGIN or x + w >= W - EDGE_MARGIN or y + h >= H - EDGE_MARGIN:
            continue
        if not (MIN_ASPECT <= w / h <= MAX_ASPECT):
            continue
        raw_boxes.append((x, y, w, h, area))

    # Pass 2: drop stray/duplicate boxes that are mostly contained inside a larger box
    # (e.g. a serif or flourish on a digit that gets picked up as its own tiny contour)
    raw_boxes.sort(key=lambda b: b[4], reverse=True)  # largest first
    kept_boxes = []
    for x, y, w, h, area in raw_boxes:
        is_duplicate = any(
            contained_ratio((x, y, w, h), kb) > CONTAINMENT_THRESHOLD
            for kb in kept_boxes
        )
        if not is_duplicate:
            kept_boxes.append((x, y, w, h))

    # Number the cubes left to right. The old (y, x) order let a cube that sat a few pixels higher
    # jump ahead of its neighbours (a row 9 1 6 4 came out as 9 4 6 1). Cubes are laid out in a row.
    kept_boxes.sort(key=lambda b: b[0])

    # Pass 3: build final cube_info list, crop, save, and annotate
    cubes = []
    for i, (x, y, w, h) in enumerate(kept_boxes):
        cx, cy = x + w // 2, y + h // 2
        digit_crop = img[y:y + h, x:x + w]

        cube_info = {
            "id": i,
            "bbox": (x, y, w, h),
            "centroid_px": (cx, cy),
            "digit_crop": digit_crop,
        }
        cubes.append(cube_info)

        crop_path = os.path.join(output_dir, f"cube_{i}_digit.png")
        cv2.imwrite(crop_path, digit_crop)

        if debug:
            cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.circle(img, (cx, cy), 4, (0, 0, 255), -1)
            cv2.putText(
                img, f"#{i}", (x, y - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2
            )

    if debug:
        debug_path = os.path.join(output_dir, "debug_annotated.png")
        cv2.imwrite(debug_path, img)
        thresh_path = os.path.join(output_dir, "debug_threshold.png")
        cv2.imwrite(thresh_path, clean)
        print(f"Saved annotated image to {debug_path}")
        print(f"Saved threshold mask to {thresh_path} (check this if detection looks off)")

    print(f"Background brightness {bg:.0f}, ink threshold {ink_thresh:.0f} (INK_FRACTION={INK_FRACTION})")
    print(f"Detected {len(cubes)} cube(s).")
    for c in cubes:
        print(f"  cube #{c['id']}: bbox={c['bbox']}  centroid(px)={c['centroid_px']}")

    return cubes


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 detect_cubes.py path/to/photo.jpg")
        sys.exit(1)

    detect_cubes(sys.argv[1])
