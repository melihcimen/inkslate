"""Key combos for Weylus' virtual keyboard, independent of the keyboard layout.

Weylus types *physical* keys: the KeyboardEvent "code" (KeyZ, BracketLeft, …) becomes the evdev
key at that position, and the desktop's layout decides which character it produces. On a German
keyboard the key Weylus calls KeyZ types "y", so a hard-coded Ctrl+Z would redo instead of undo.

Inkslate therefore stores shortcuts as keyvals ("z" + Ctrl) and resolves them through the keymap
of the running desktop right before sending them to the tablet page. Characters that need AltGr
(e.g. "[" on Turkish or German layouts) are sent with AltRight held.
"""

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

# evdev key code -> KeyboardEvent.code, from Weylus' own mapping (src/input/uinput_device.rs).
EVDEV_TO_CODE = {
    1: "Escape", 2: "Digit1", 3: "Digit2", 4: "Digit3", 5: "Digit4", 6: "Digit5", 7: "Digit6",
    8: "Digit7", 9: "Digit8", 10: "Digit9", 11: "Digit0", 12: "Minus", 13: "Equal",
    14: "Backspace", 15: "Tab", 16: "KeyQ", 17: "KeyW", 18: "KeyE", 19: "KeyR", 20: "KeyT",
    21: "KeyY", 22: "KeyU", 23: "KeyI", 24: "KeyO", 25: "KeyP", 26: "BracketLeft",
    27: "BracketRight", 28: "Enter", 29: "ControlLeft", 30: "KeyA", 31: "KeyS", 32: "KeyD",
    33: "KeyF", 34: "KeyG", 35: "KeyH", 36: "KeyJ", 37: "KeyK", 38: "KeyL", 39: "Semicolon",
    40: "Quote", 41: "Backquote", 42: "ShiftLeft", 43: "Backslash", 44: "KeyZ", 45: "KeyX",
    46: "KeyC", 47: "KeyV", 48: "KeyB", 49: "KeyN", 50: "KeyM", 51: "Comma", 52: "Period",
    53: "Slash", 54: "ShiftRight", 55: "NumpadMultiply", 56: "AltLeft", 57: "Space",
    58: "CapsLock", 59: "F1", 60: "F2", 61: "F3", 62: "F4", 63: "F5", 64: "F6", 65: "F7", 66: "F8",
    67: "F9", 68: "F10", 69: "NumLock", 70: "ScrollLock", 71: "Numpad7", 72: "Numpad8",
    73: "Numpad9", 74: "NumpadSubtract", 75: "Numpad4", 76: "Numpad5", 77: "Numpad6",
    78: "NumpadAdd", 79: "Numpad1", 80: "Numpad2", 81: "Numpad3", 82: "Numpad0",
    86: "IntlBackslash", 87: "F11", 88: "F12", 89: "IntlRo", 90: "KanaMode", 96: "NumpadEnter",
    97: "ControlRight", 98: "NumpadDivide", 99: "PrintScreen", 100: "AltRight", 102: "Home",
    103: "ArrowUp", 104: "PageUp", 105: "ArrowLeft", 106: "ArrowRight", 107: "End",
    108: "ArrowDown", 109: "PageDown", 110: "Insert", 111: "Delete", 113: "AudioVolumeMute",
    114: "AudioVolumeDown", 115: "AudioVolumeUp", 116: "Power", 117: "NumpadEqual", 119: "Pause",
    121: "NumpadComma", 123: "Lang2", 124: "IntlYen", 125: "OSLeft", 126: "OSRight", 129: "Again",
    130: "Props", 131: "Undo", 133: "Copy", 134: "Open", 135: "Paste", 136: "Find", 137: "Cut",
    138: "Help", 139: "ContextMenu", 142: "Sleep", 143: "WakeUp", 155: "LaunchMail", 161: "Eject",
    163: "MediaTrackNext", 164: "MediaPlayPause", 165: "MediaTrackPrevious", 166: "MediaStop",
    179: "NumpadParenLeft", 180: "NumpadParenRight", 183: "F13", 184: "F14", 185: "F15",
    186: "F16", 187: "F17", 188: "F18", 189: "F19", 190: "F20", 191: "F21", 192: "F22", 193: "F23",
    194: "F24", 223: "Cancel", 226: "LaunchMediaPlayer",
}
CODE_TO_EVDEV = {code: ev for ev, code in EVDEV_TO_CODE.items()}

# Fallback when no desktop keymap is available (tests, headless): US layout.
_US_SHIFTED = {
    "exclam": "1", "at": "2", "numbersign": "3", "dollar": "4", "percent": "5",
    "asciicircum": "6", "ampersand": "7", "asterisk": "8", "parenleft": "9", "parenright": "0",
    "underscore": "minus", "plus": "equal", "braceleft": "bracketleft",
    "braceright": "bracketright", "colon": "semicolon", "quotedbl": "apostrophe",
    "less": "comma", "greater": "period", "question": "slash", "bar": "backslash",
    "asciitilde": "grave",
}
_US_CODES = {
    "minus": "Minus", "equal": "Equal", "bracketleft": "BracketLeft", "bracketright": "BracketRight",
    "semicolon": "Semicolon", "apostrophe": "Quote", "grave": "Backquote", "backslash": "Backslash",
    "comma": "Comma", "period": "Period", "slash": "Slash", "space": "Space", "Return": "Enter",
    "Tab": "Tab", "BackSpace": "Backspace", "Delete": "Delete", "Escape": "Escape",
    "Home": "Home", "End": "End", "Page_Up": "PageUp", "Page_Down": "PageDown",
    "Left": "ArrowLeft", "Right": "ArrowRight", "Up": "ArrowUp", "Down": "ArrowDown",
    "Insert": "Insert", "Control_L": "ControlLeft", "Shift_L": "ShiftLeft", "Alt_L": "AltLeft",
}

MODIFIERS = ("ctrl", "shift", "alt", "meta")


def combo(keyval, ctrl=False, shift=False, alt=False, meta=False):
    """A layout-independent shortcut: a keyval name (see gdkkeysyms.h) plus modifiers."""
    return {"keyval": keyval, "ctrl": ctrl, "shift": shift, "alt": alt, "meta": meta}


def _display():
    Gtk.init()
    return Gdk.Display.get_default()


def _us_lookup(name):
    shift = False
    if name in _US_SHIFTED:
        name, shift = _US_SHIFTED[name], True
    if len(name) == 1 and name.isalpha():
        if name.isupper():
            shift = True
        return "Key" + name.upper(), shift
    if len(name) == 1 and name.isdigit():
        return "Digit" + name, shift
    if name.startswith("F") and name[1:].isdigit():
        return name, shift
    return _US_CODES.get(name), shift


def resolve(spec, display=None):
    """Turn a combo() into what the tablet page sends: {code, key, ctrl, shift, alt, meta, altgr}.

    Specs that already carry a physical "code" are passed through unchanged."""
    if not spec:
        return None
    if "code" in spec:
        return dict(spec)
    name = spec.get("keyval", "")
    keyval = Gdk.keyval_from_name(name)
    char = chr(Gdk.keyval_to_unicode(keyval)) if Gdk.keyval_to_unicode(keyval) else ""
    out = {m: bool(spec.get(m)) for m in MODIFIERS}
    out.update(key=char, altgr=False)

    entry = None
    display = _display() if display is None else display   # False: no keymap, use US
    if display and keyval:
        ok, keys = display.map_keyval(keyval)
        if ok and keys:
            # The first layout group is the active one for single-layout setups; within it,
            # take the lowest shift level (plain key before Shift before AltGr).
            group = min(k.group for k in keys)
            entry = min((k for k in keys if k.group == group), key=lambda k: k.level)
    code = EVDEV_TO_CODE.get(entry.keycode - 8) if entry else None
    if code:
        out["code"] = code
        if entry.level in (1, 3):
            out["shift"] = True
        out["altgr"] = entry.level >= 2
    else:
        code, shift = _us_lookup(name)
        if not code:
            return None
        out["code"] = code
        out["shift"] = out["shift"] or shift
    return out


# Modifier keys on their own, with the names shown for them.
MODIFIER_KEYS = {"Control_L": "Ctrl", "Control_R": "Right Ctrl", "Shift_L": "Shift",
                 "Shift_R": "Right Shift", "Alt_L": "Alt", "Alt_R": "Right Alt", "Super_L": "Super",
                 "Super_R": "Right Super", "Meta_L": "Meta", "ISO_Level3_Shift": "AltGr"}


def is_modifier(keyval):
    return Gdk.keyval_name(keyval) in MODIFIER_KEYS


def from_key_event(keyval, state):
    """Record a shortcut pressed in the PC app (Gtk.EventControllerKey) as a combo().

    Returns None for a modifier on its own (the caller may record it on release with
    modifier_combo())."""
    mods = state & Gtk.accelerator_get_default_mod_mask()
    lower = Gdk.keyval_to_lower(keyval)
    if is_modifier(lower):
        return None
    return combo(Gdk.keyval_name(lower),
                 ctrl=bool(mods & Gdk.ModifierType.CONTROL_MASK),
                 shift=bool(mods & Gdk.ModifierType.SHIFT_MASK),
                 alt=bool(mods & Gdk.ModifierType.ALT_MASK),
                 meta=bool(mods & (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK)))


def modifier_combo(keyval):
    """A modifier key pressed and released alone, e.g. Shift to hold for straight lines."""
    name = Gdk.keyval_name(keyval)
    return combo(name) if name in MODIFIER_KEYS and name != "ISO_Level3_Shift" else None


def label(spec):
    """Human-readable text for a combo(), e.g. "Ctrl+Shift+Z"."""
    if not spec:
        return ""
    if spec.get("keyval") in MODIFIER_KEYS:
        return MODIFIER_KEYS[spec["keyval"]]
    if "code" in spec:
        name = spec.get("key") or spec["code"]
        keyval = Gdk.unicode_to_keyval(ord(name)) if len(name) == 1 else Gdk.keyval_from_name(name)
    else:
        keyval = Gdk.keyval_from_name(spec.get("keyval", ""))
    mods = Gdk.ModifierType(0)
    if spec.get("ctrl"):
        mods |= Gdk.ModifierType.CONTROL_MASK
    if spec.get("shift"):
        mods |= Gdk.ModifierType.SHIFT_MASK
    if spec.get("alt"):
        mods |= Gdk.ModifierType.ALT_MASK
    if spec.get("meta"):
        mods |= Gdk.ModifierType.SUPER_MASK
    return Gtk.accelerator_get_label(keyval, mods) or spec.get("keyval", "?")
