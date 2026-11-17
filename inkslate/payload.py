"""Builds the config object sent to the tablet page from the saved settings.

Shortcuts are resolved for the desktop's keyboard layout (keys.py), toolbar buttons get their
kind, icon and title, and the screen area is turned into coordinates Weylus understands.
"""

import copy

from . import keys as keymod
from .profiles import ACTIONS, action_name, action_short, profile_keys

FULL = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}


def build(config, resolve=keymod.resolve, monitors=(), default_accent="#3584e4"):
    t = copy.deepcopy(config["tablet"])
    resolved = {}
    for action, spec in profile_keys(config["profile"]).items():
        combo = resolve(spec)
        if combo:
            resolved[action] = combo
    t["keys"] = resolved

    buttons = []
    for b in t["toolbar"].get("buttons", []):
        action = b.get("action", "none")
        kind = ACTIONS.get(action)
        if kind in (None, "none"):
            continue
        out = {"action": action, "kind": kind, "icon": action, "title": action_name(action),
               "short": action_short(action)}
        if action in ("custom", "custom_hold"):
            out["keys"] = resolve(b.get("keys"))
            out["label"] = b.get("label") or keymod.label(b.get("keys"))
            out["title"] = out["short"] = out["label"]
            if not out["keys"]:
                continue
        elif kind in ("key", "hold"):
            out["keys"] = resolved.get(action)
        buttons.append(out)
    t["toolbar"]["buttons"] = buttons
    t["toolbar"]["accent"] = t["toolbar"].get("accent") or default_accent
    t["area"] = area(t["area"], monitors)
    return t



def monitor_rect(monitors, connector):
    """(x, y, w, h) of the monitor, or of all monitors together for "" / unknown."""
    if not monitors:
        return (0, 0, 1920, 1080)
    for m in monitors:
        if connector and m["connector"] == connector:
            return (m["x"], m["y"], m["w"], m["h"])
    x0 = min(m["x"] for m in monitors)
    y0 = min(m["y"] for m in monitors)
    x1 = max(m["x"] + m["w"] for m in monitors)
    y1 = max(m["y"] + m["h"] for m in monitors)
    return (x0, y0, x1 - x0, y1 - y0)


def area(cfg_area, monitors):
    """Where the tablet maps to, relative to everything Weylus captures ("Desktop": all monitors),
    plus the pixel aspect ratio of that rectangle when the tablet should keep it."""
    bx, by, bw, bh = monitor_rect(monitors, "")
    mx, my, mw, mh = monitor_rect(monitors, cfg_area.get("monitor", ""))
    r = dict(FULL, **(cfg_area.get("region") or {}))
    r["w"] = min(max(r["w"], 0.02), 1.0)
    r["h"] = min(max(r["h"], 0.02), 1.0)
    r["x"] = min(max(r["x"], 0.0), 1.0 - r["w"])
    r["y"] = min(max(r["y"], 0.0), 1.0 - r["h"])
    display = {
        "x": (mx - bx + r["x"] * mw) / bw,
        "y": (my - by + r["y"] * mh) / bh,
        "w": r["w"] * mw / bw,
        "h": r["h"] * mh / bh,
    }
    aspect = (r["w"] * mw) / (r["h"] * mh) if cfg_area.get("keep_aspect") else 0
    return {"display": {k: round(v, 6) for k, v in display.items()}, "aspect": round(aspect, 6)}
