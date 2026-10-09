"""
sorter.py
---------
Turns a list of detected cubes (id, digit, centroid_px) into an ordered
pick-and-place plan for the robotic arm, optimized for minimal total travel.

Design:
  1. Every cube gets a "group key" based on the chosen sort mode.
     Groups are then ordered (ascending by key) to decide slot blocks.
  2. Within a group, cube order doesn't matter for correctness, so cubes
     are greedily matched to the nearest free slot in that group's block.
  3. The resulting (pick, place) jobs are sequenced with a greedy
     nearest-neighbor walk to minimize total arm travel distance.

Coordinates are currently pixel coordinates from the camera frame, used
as a stand-in until camera->arm calibration (project step 7) is done.
Swap `pixel_to_arm` for the real transform once you have it; everything
downstream (slots, planning, sequencing) is coordinate-system agnostic.
"""

import math
import itertools

# ---------------------------------------------------------------------------
# CONFIG — placeholders until arm calibration + physical layout are finalized
# ---------------------------------------------------------------------------

# Where the arm "starts" before a scan (e.g. resting/home position), in the
# same coordinate space as cube centroids and destination slots.
HOME_POS = (0, 0)

# Destination side layout: a single row of evenly spaced slots.
# TODO: replace with real arm-space coordinates once calibrated.
DEST_ORIGIN = (700, 50)   # (x, y) of slot 0
DEST_SPACING = 60         # px between consecutive slots
DEST_ROW_Y_JITTER = 0     # keep 0 unless you want a zig-zag row


def get_dest_slots(n):
    """Generate n destination slot coordinates in a single row."""
    ox, oy = DEST_ORIGIN
    return [(ox + i * DEST_SPACING, oy) for i in range(n)]


def pixel_to_arm(px):
    """
    Placeholder identity transform. Replace with your real camera->arm
    calibration (homography / affine transform) once built.
    """
    return px


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ---------------------------------------------------------------------------
# GROUP KEY LOGIC — one function per sort mode
# ---------------------------------------------------------------------------

def _parse_custom_order(custom_order_str):
    """'5,3,1,0,2' -> [5,3,1,0,2] (dedup, ints only, ignores junk)."""
    if not custom_order_str:
        return []
    out = []
    for tok in custom_order_str.split(","):
        tok = tok.strip()
        if tok.isdigit():
            d = int(tok)
            if d not in out:
                out.append(d)
    return out


def compute_group_keys(cubes, mode, custom_order_str=None):
    """
    Returns dict: cube_id -> sortable group key.
    Cubes with equal keys land in the same contiguous slot block, and are
    free to be assigned to any slot within it (order among them is decided
    later purely to minimize travel).
    """
    digits = {c["id"]: c["predicted_digit"] for c in cubes}

    if mode == "ascending":
        return {cid: (d,) for cid, d in digits.items()}

    if mode == "descending":
        return {cid: (-d,) for cid, d in digits.items()}

    if mode == "odd_even":
        # evens first (parity 0), then odds (parity 1); ascending within each
        return {cid: (d % 2, d) for cid, d in digits.items()}

    if mode == "group_by_digit":
        # groups ordered by the order each digit value FIRST appears on the
        # tray (detection order / id) -- pure clustering, no numeric bias.
        first_seen = {}
        for c in sorted(cubes, key=lambda c: c["id"]):
            d = c["predicted_digit"]
            if d not in first_seen:
                first_seen[d] = len(first_seen)
        return {cid: (first_seen[d],) for cid, d in digits.items()}

    if mode == "custom":
        order = _parse_custom_order(custom_order_str)
        priority = {d: i for i, d in enumerate(order)}
        base = len(order)
        # digits not mentioned by the user: appended after, ascending by value
        return {
            cid: (priority[d],) if d in priority else (base + d,)
            for cid, d in digits.items()
        }

    raise ValueError(f"Unknown sort mode: {mode}")


# ---------------------------------------------------------------------------
# SLOT ASSIGNMENT (within-group, greedy nearest match)
# ---------------------------------------------------------------------------

def assign_slots(cubes, group_keys, dest_slots):
    """
    Buckets cubes into contiguous slot blocks by group key (ascending),
    then greedily matches cubes to slots *within their block* to minimize
    pick->slot distance. Returns dict: cube_id -> slot (x, y), slot_index.
    """
    # order groups ascending by key
    distinct_keys = sorted(set(group_keys.values()))
    grouped = {k: [c for c in cubes if group_keys[c["id"]] == k] for k in distinct_keys}

    assignment = {}
    slot_cursor = 0
    for key in distinct_keys:
        members = grouped[key]
        block = dest_slots[slot_cursor: slot_cursor + len(members)]
        block_indices = list(range(slot_cursor, slot_cursor + len(members)))
        slot_cursor += len(members)

        # greedy min-distance matching within this block
        remaining_cubes = members[:]
        remaining_slots = list(zip(block_indices, block))
        while remaining_cubes:
            best = None
            best_dist = None
            for c in remaining_cubes:
                for idx, slot_pos in remaining_slots:
                    d = dist(c["centroid_px"], slot_pos)
                    if best_dist is None or d < best_dist:
                        best_dist = d
                        best = (c, idx, slot_pos)
            c, idx, slot_pos = best
            assignment[c["id"]] = {"slot_index": idx, "slot_pos": slot_pos}
            remaining_cubes.remove(c)
            remaining_slots = [(i, p) for i, p in remaining_slots if i != idx]

    return assignment


# ---------------------------------------------------------------------------
# SEQUENCING (greedy nearest-neighbor pick/place walk)
# ---------------------------------------------------------------------------

def sequence_jobs(jobs, start_pos=HOME_POS):
    """
    jobs: list of {cube_id, digit, pick, place, slot_index, group_rank}
    Greedy nearest-neighbor: from current arm position, go to whichever
    job's pick point is closest, then to its place point, repeat.
    Returns (ordered_jobs, total_distance).
    """
    remaining = jobs[:]
    pos = start_pos
    ordered = []
    total = 0.0
    while remaining:
        nxt = min(remaining, key=lambda j: dist(pos, j["pick"]))
        total += dist(pos, nxt["pick"])
        total += dist(nxt["pick"], nxt["place"])
        pos = nxt["place"]
        ordered.append(nxt)
        remaining.remove(nxt)
    return ordered, total


def naive_baseline_distance(jobs, start_pos=HOME_POS):
    """Distance if jobs were done in original detection (id) order — for comparison."""
    pos = start_pos
    total = 0.0
    for j in sorted(jobs, key=lambda j: j["cube_id"]):
        total += dist(pos, j["pick"])
        total += dist(j["pick"], j["place"])
        pos = j["place"]
    return total


# ---------------------------------------------------------------------------
# PUBLIC ENTRY POINT
# ---------------------------------------------------------------------------

def plan_sort(cubes, mode, custom_order_str=None, start_pos=HOME_POS):
    """
    cubes: list of {id, predicted_digit, centroid_px, ...} (from detect_cubes + CNN)
    mode: 'ascending' | 'descending' | 'odd_even' | 'group_by_digit' | 'custom'
    custom_order_str: e.g. "5,3,1,0,2,4,6,7,8,9" (only used when mode == 'custom')

    Returns dict with the ordered plan + distance stats.
    """
    if not cubes:
        return {"steps": [], "total_distance": 0, "baseline_distance": 0, "savings_pct": 0}

    group_keys = compute_group_keys(cubes, mode, custom_order_str)
    dest_slots = get_dest_slots(len(cubes))
    assignment = assign_slots(cubes, group_keys, dest_slots)

    jobs = []
    for c in cubes:
        a = assignment[c["id"]]
        jobs.append({
            "cube_id": c["id"],
            "digit": c["predicted_digit"],
            "pick": tuple(c["centroid_px"]),
            "place": a["slot_pos"],
            "slot_index": a["slot_index"],
            "group_rank": group_keys[c["id"]],
        })

    ordered_jobs, total = sequence_jobs(jobs, start_pos=start_pos)
    baseline = naive_baseline_distance(jobs, start_pos=start_pos)
    savings_pct = round(100 * (baseline - total) / baseline, 1) if baseline > 0 else 0

    steps = []
    for i, j in enumerate(ordered_jobs, start=1):
        steps.append({
            "step": i,
            "cube_id": j["cube_id"],
            "digit": j["digit"],
            "pick_px": j["pick"],
            "place_px": j["place"],
            "slot_index": j["slot_index"],
        })

    return {
        "mode": mode,
        "steps": steps,
        "total_distance": round(total, 1),
        "baseline_distance": round(baseline, 1),
        "savings_pct": savings_pct,
    }
