import gettext
import json
import os
from pathlib import Path

_LOCALE_DIRS = [
    Path(__file__).resolve().parent / "locale",          # source checkout / user install
    Path.home() / ".local" / "share" / "locale",
    Path("/usr/share/locale"),
]

LANGUAGES = {"": None, "en": "English", "tr": "Türkçe"}   # "" follows the system


def configured_language():
    """The "language" setting, read directly so translations work before Config is loaded."""
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    try:
        lang = json.loads((base / "inkslate" / "config.json").read_text()).get("language", "")
    except (OSError, ValueError):
        return ""
    return lang if lang in LANGUAGES else ""


def _translation():
    lang = configured_language()
    if lang == "en":
        return gettext.NullTranslations()
    for d in _LOCALE_DIRS:
        t = gettext.translation("inkslate", localedir=d, languages=[lang] if lang else None,
                                fallback=True)
        if isinstance(t, gettext.GNUTranslations):
            return t
    return gettext.NullTranslations()


_ = _translation().gettext
