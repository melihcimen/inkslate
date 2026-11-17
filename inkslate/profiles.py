"""Toolbar/gesture actions and per-app shortcut presets.

Shortcuts are layout-independent keyval combos (see keys.py), resolved to physical keys for the
desktop's keyboard layout right before they are sent to the tablet page.
"""

from .i18n import _
from .keys import combo

# Action kinds decide how a toolbar button behaves on the tablet:
#   key     tap sends the shortcut
#   hold    the key stays down while the button is held (e.g. Space to pan)
#   toggle  tap toggles, holding works only while held (eraser)
#   penmode tap: the next pen stroke is a right/middle click; holding: every stroke while held
#   page    something on the tablet page itself
ACTIONS = {
    "undo": "key", "redo": "key", "save": "key", "copy": "key", "paste": "key",
    "zoom_in": "key", "zoom_out": "key", "brush_bigger": "key", "brush_smaller": "key",
    "pan": "hold",
    "eraser": "toggle",
    "right_click": "penmode", "middle_drag": "penmode",
    "touch": "page", "fullscreen": "page",
    "custom": "key", "custom_hold": "hold",
    "none": "none",
}
KEY_ACTIONS = ["undo", "redo", "save", "copy", "paste", "zoom_in", "zoom_out",
               "brush_bigger", "brush_smaller", "pan"]
GESTURE_ACTIONS = [a for a, kind in ACTIONS.items()
                   if kind in ("key", "toggle", "penmode", "page") and not a.startswith("custom")] + ["none"]
MAX_BUTTONS = 10

_BASE = {
    "undo": combo("z", ctrl=True),
    "redo": combo("z", ctrl=True, shift=True),
    "save": combo("s", ctrl=True),
    "copy": combo("c", ctrl=True),
    "paste": combo("v", ctrl=True),
    "zoom_in": combo("plus", ctrl=True),
    "zoom_out": combo("minus", ctrl=True),
    "brush_bigger": combo("bracketright"),
    "brush_smaller": combo("bracketleft"),
    "pan": combo("space"),
}


def _profile(name, **overrides):
    keys = dict(_BASE)
    keys.update(overrides)
    return {"name": name, "keys": {k: v for k, v in keys.items() if v is not None}}


PROFILES = {
    "generic": _profile(None),
    "krita": _profile("Krita", zoom_in=combo("plus"), zoom_out=combo("minus")),
    "gimp": _profile("GIMP", redo=combo("y", ctrl=True), zoom_in=combo("plus"), zoom_out=combo("minus")),
    "inkscape": _profile("Inkscape", zoom_in=combo("plus"), zoom_out=combo("minus"),
                         brush_bigger=None, brush_smaller=None),
    "xournalpp": _profile("Xournal++", redo=combo("y", ctrl=True), brush_bigger=None,
                          brush_smaller=None, pan=None),
    "rnote": _profile("Rnote", brush_bigger=None, brush_smaller=None, pan=None),
}


def profile_name(profile_id):
    name = PROFILES[profile_id]["name"]
    return name if name else _("Generic")


def profile_keys(profile_id):
    """The profile's shortcuts; actions it doesn't define fall back to the generic ones."""
    keys = dict(PROFILES["generic"]["keys"])
    keys.update(PROFILES.get(profile_id, PROFILES["generic"])["keys"])
    return keys


def action_name(action):
    return {
        "undo": _("Undo"),
        "redo": _("Redo"),
        "save": _("Save"),
        "copy": _("Copy"),
        "paste": _("Paste"),
        "zoom_in": _("Zoom in"),
        "zoom_out": _("Zoom out"),
        "brush_bigger": _("Bigger brush"),
        "brush_smaller": _("Smaller brush"),
        "pan": _("Pan (hold Space)"),
        "eraser": _("Eraser"),
        "right_click": _("Right click"),
        "middle_drag": _("Middle-button drag"),
        "touch": _("Toggle finger input"),
        "fullscreen": _("Fullscreen"),
        "custom": _("Custom shortcut"),
        "custom_hold": _("Custom key, held"),
        "none": _("Nothing"),
    }[action]


def action_short(action):
    """Label under the icon on the tablet toolbar, where space is tight."""
    short = {
        "zoom_in": _("Zoom +"),
        "zoom_out": _("Zoom −"),
        "brush_bigger": _("Brush +"),
        "brush_smaller": _("Brush −"),
        "pan": _("Pan"),
        "middle_drag": _("Middle"),
        "touch": _("Touch"),
    }
    return short.get(action) or action_name(action)


def action_hint(action):
    return {
        "eraser": _("Tap: on/off · hold: only while held"),
        "right_click": _("Tap: next pen tap is a right click · hold: while held"),
        "middle_drag": _("Tap: next pen stroke drags with the middle button · hold: while held"),
        "pan": _("Hold the button and drag with the pen"),
        "custom_hold": _("The key stays down while the button is held"),
    }.get(action, "")
