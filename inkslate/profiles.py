"""Shortcut profiles for common drawing and note-taking apps.

Key combos are sent through Weylus' virtual keyboard, which uses physical key positions
(evdev codes as on a US layout), not the characters of the active layout.
"""

from .i18n import _

CTRL_Z = {"code": "KeyZ", "key": "z", "ctrl": True}
CTRL_SHIFT_Z = {"code": "KeyZ", "key": "Z", "ctrl": True, "shift": True}
CTRL_Y = {"code": "KeyY", "key": "y", "ctrl": True}
CTRL_S = {"code": "KeyS", "key": "s", "ctrl": True}

PROFILES = {
    "rnote": {
        "name": "Rnote",
        "keys": {"undo": CTRL_Z, "redo": CTRL_SHIFT_Z, "save": CTRL_S},
    },
    "xournalpp": {
        "name": "Xournal++",
        "keys": {"undo": CTRL_Z, "redo": CTRL_Y, "save": CTRL_S},
    },
    "krita": {
        "name": "Krita",
        "keys": {"undo": CTRL_Z, "redo": CTRL_SHIFT_Z, "save": CTRL_S},
    },
    "gimp": {
        "name": "GIMP",
        "keys": {"undo": CTRL_Z, "redo": CTRL_Y, "save": CTRL_S},
    },
    "generic": {
        "name": None,  # translated at runtime
        "keys": {"undo": CTRL_Z, "redo": CTRL_SHIFT_Z, "save": CTRL_S},
    },
}

# Actions a toolbar button or gesture can trigger. Key actions come from the profile.
ACTIONS = ["undo", "redo", "eraser", "save", "touch", "fullscreen", "none"]


def profile_name(profile_id):
    name = PROFILES[profile_id]["name"]
    return name if name else _("Generic")


def action_name(action):
    return {
        "undo": _("Undo"),
        "redo": _("Redo"),
        "eraser": _("Eraser"),
        "save": _("Save"),
        "touch": _("Toggle finger input"),
        "fullscreen": _("Fullscreen"),
        "none": _("Nothing"),
    }[action]
