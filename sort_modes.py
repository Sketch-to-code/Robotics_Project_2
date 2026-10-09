"""
sort_modes.py
-------------
Decides which digit belongs in which drop slot for each sorting mode.

    slot_targets([1, 4, 3], "ascending")   -> [1, 3, 4]   smallest in slot 0, largest in slot 2
    slot_targets([1, 4, 3], "descending")  -> [4, 3, 1]   largest in slot 0, smallest in slot 2
    slot_targets([1, 4, 3], "evenodd")     -> [4, 1, 3]   evens on the slot-0 side, odds on the slot-2 side

Even/odd with 3 slots: evens fill from slot 0 towards the middle and odds from slot 2 towards
the middle (each group in ascending order), so with e.g. one even and two odds the second odd
lands in the middle slot 1. Digit 0 counts as even.
"""

MODES = ("ascending", "descending", "evenodd")


def normalize_mode(mode):
    m = (mode or "ascending").strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    if m in ("asc", "ascending"):
        return "ascending"
    if m in ("desc", "descending"):
        return "descending"
    if m in ("evenodd", "oddeven", "parity"):
        return "evenodd"
    raise ValueError(f"Unknown mode '{mode}'. Use one of: {', '.join(MODES)}")


def slot_targets(digits, mode):
    """Return the digit that must end up in slot 0, 1, 2, ... (same length as digits)."""
    d = [int(x) for x in digits]
    mode = normalize_mode(mode)
    if mode == "ascending":
        return sorted(d)
    if mode == "descending":
        return sorted(d, reverse=True)
    evens = sorted(x for x in d if x % 2 == 0)
    odds = sorted(x for x in d if x % 2 == 1)
    return evens + odds
