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
    "language": "",                # "" = system language, or "en" / "tr"
    "mode": "usb",                 # "usb": localhost + adb reverse, "wifi": LAN with access code
    "access_code": "",
    "weylus_path": "",             # empty: look in PATH and ~/.local/bin
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
    "profile": "rnote",
    # Pushed to the tablet page as-is (see DEFAULTS in web/inkslate.js).
    "tablet": {
        "curve": {"in_min": 0.0, "in_max": 1.0, "gamma": 1.0},
        "smoothing": 0.0,
        "eraser": False,
        "palm": {"enabled": True, "ms": 500},
        "gestures": {"enabled": True, "two": "undo", "three": "redo"},
        "toolbar": {
            "enabled": True,
            "side": "left",
            "buttons": [{"action": "undo"}, {"action": "redo"}, {"action": "eraser"}],
        },
        "wake_lock": True,
        "fullscreen_on_tap": False,
        "hide_menu": True,
    },
}


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

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def tablet_config(self):
        """The config object sent to the tablet page, with the profile's key bindings."""
        t = copy.deepcopy(self.data["tablet"])
        t["keys"] = copy.deepcopy(PROFILES[self.data["profile"]]["keys"])
        return t
