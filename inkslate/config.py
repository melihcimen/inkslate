"""Persistent configuration (~/.config/inkslate/config.json)."""

import copy
import json
import logging
import os
import secrets
from pathlib import Path

from .profiles import PROFILES

log = logging.getLogger("inkslate")

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "inkslate"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "inkslate"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "inkslate"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS = {
    "mode": "usb",                 # "usb": localhost + adb reverse, "wifi": LAN with access code
    "access_code": "",
    "weylus_path": "",             # a path, "flatpak" (Community Edition), or empty: PATH,
                                   # ~/.local/bin, then the Flatpak
    "web_port": 1701,
    "websocket_port": 9001,
    "control_port": 9002,
    "video": {
        "wayland_support": False,  # PipeWire capture; asks for screen-sharing permission
        "try_vaapi": False,
        "try_nvenc": False,
    },
    "usb": {
        "auto_open": True,         # open the page on the tablet when it is plugged in
        "auto_fullscreen": True,   # switch the page to fullscreen through Chrome DevTools
        "dim_on_energysaving": False,
        "dim_level": 10,           # percent of the tablet's brightness range
    },
    "profile": "generic",          # shortcut preset, see profiles.py
    "appearance": {"theme": "system"},   # this window: "system", "light" or "dark"
    "tablet_screen": [1280, 800],  # last tablet page size seen (CSS px), for the previews
    # Sent to the tablet page (see DEFAULTS in web/inkslate.js) after payload.build() resolves
    # shortcuts for the keyboard layout and the screen area for the monitors.
    "tablet": {
        "curve": {"in_min": 0.0, "in_max": 1.0, "gamma": 1.0},
        "smoothing": 0.0,
        # a pen tap stays a click: the position is held until the tip moves more than `radius`
        # CSS px or `ms` pass (the tip slips a little when it presses the glass)
        "tap": {"enabled": True, "radius": 8, "ms": 300},
        "eraser": False,
        "palm": {"enabled": True, "ms": 500},
        "gestures": {"enabled": True, "two": "undo", "three": "redo"},
        "toolbar": {
            "enabled": True,
            "side": "left",        # left, right, top, bottom
            "size": 54,            # button size in CSS pixels
            "opacity": 0.6,        # while not touched
            "theme": "dark",       # dark or light
            "accent": "",          # "" follows the desktop's accent color
            "labels": False,
            "fade": True,          # almost hide the bar while the pen is drawing
            "autohide": False,     # wait at the edge as a thin strip; a hovering pen or a tap brings it out
            "buttons": [{"action": "undo"}, {"action": "redo"}, {"action": "eraser"}],
        },
        "screen": {
            "background": "#000000",   # energy-saving screen
            "show_area": True,         # mark the unused part when the area keeps the aspect ratio
        },
        "area": {
            "monitor": "",             # connector name, "" = all screens
            "region": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},   # part of that monitor
            "keep_aspect": False,      # use only the part of the tablet with the same shape
        },
        "wake_lock": True,
        "fullscreen_on_tap": False,
        "hide_menu": True,
    },
}


OBSOLETE = ("language",)   # settings that no longer exist


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path=CONFIG_FILE):
        self.path = Path(path)
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self):
        try:
            self.data = _merge(DEFAULTS, json.loads(self.path.read_text()))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("could not read %s: %s; using defaults", self.path, e)
        if not self.data["access_code"]:
            self.data["access_code"] = secrets.token_urlsafe(6)
        if self.data["profile"] not in PROFILES:
            self.data["profile"] = "generic"
        for key in OBSOLETE:
            self.data.pop(key, None)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value
