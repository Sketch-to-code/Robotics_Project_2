"""
calibrate.py
------------
Run this once the physical arm is placed on the table, to teach the
system how a pixel in the overhead photo maps to a real-world position
relative to the arm's base.

Usage:
    python calibrate.py path/to/an/overhead_photo.png

What it does:
  1. Shows you the photo. Click on 4+ points you can also measure by hand
     (e.g. the 4 corners of a placemat, or a few marked spots on the
     table). Close the plot window when done clicking.
  2. For each point you clicked, it asks you to type the REAL-WORLD (x, y)
     position in centimeters, measured with a ruler/tape from the arm's
     base (arm base = origin (0, 0); pick consistent +x/+y directions,
     e.g. +x = to the arm's right, +y = straight out in front of it).
  3. Saves the resulting homography to calibration.json.

The more spread-out and accurate your clicked points + measurements are,
the better pixel_to_arm() will work across the whole table.
"""

import sys

import matplotlib.pyplot as plt
from PIL import Image

from calibration import save_calibration


def main():
    if len(sys.argv) != 2:
        print("Usage: python calibrate.py path/to/overhead_photo.png")
        sys.exit(1)

    image_path = sys.argv[1]
    img = Image.open(image_path)

    print("A window will open showing your photo.")
    print("Click at least 4 points you can measure by hand on the real table.")
    print("Press Enter in this terminal once you're done clicking, or just")
    print("close the plot window after your last click.\n")

    fig, ax = plt.subplots()
    ax.imshow(img)
    ax.set_title("Click 4+ reference points, then close this window")
    pixel_points = plt.ginput(n=-1, timeout=0)
    plt.close(fig)

    if len(pixel_points) < 4:
        print(f"Only got {len(pixel_points)} points -- need at least 4. Re-run and click more.")
        sys.exit(1)

    print(f"\nGot {len(pixel_points)} pixel points: {pixel_points}\n")
    print("Now enter the REAL-WORLD position of each point, in centimeters,")
    print("measured from the arm's base (origin = arm base = (0, 0)).\n")

    world_points = []
    for i, (px, py) in enumerate(pixel_points):
        print(f"Point {i + 1} was clicked at pixel ({px:.0f}, {py:.0f})")
        x_cm = float(input(f"  real-world x (cm) for point {i + 1}: "))
        y_cm = float(input(f"  real-world y (cm) for point {i + 1}: "))
        world_points.append((x_cm, y_cm))

    save_calibration(pixel_points, world_points)
    print("\nSaved calibration.json. pixel_to_arm() will now use this mapping.")


if __name__ == "__main__":
    main()
