#!/usr/bin/env python3
"""Unit tests without Weylus or a tablet: shortcuts, the tablet payload, the QR encoder.

Run: python3 tests/unit_test.py   (the QR check needs python3-opencv, it is skipped without it)
"""

import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="inkslate-unit-"))
for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = str(TMP / var.lower())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inkslate import keys, payload, qr  # noqa: E402
from inkslate.config import DEFAULTS, Config  # noqa: E402
from inkslate.icons import ICONS  # noqa: E402
from inkslate.profiles import ACTIONS, PROFILES, profile_keys  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


# ---- keys: the US fallback table (no display needed)
us = lambda spec: keys.resolve(spec, display=False)  # noqa: E731
r = us(keys.combo("z", ctrl=True))
check("Ctrl+Z -> KeyZ", r["code"] == "KeyZ" and r["ctrl"] and not r["shift"], str(r))
r = us(keys.combo("plus", ctrl=True))
check("Ctrl+plus on US -> Shift+Equal", r["code"] == "Equal" and r["shift"] and r["ctrl"], str(r))
r = us(keys.combo("bracketleft"))
check("[ on US -> BracketLeft", r["code"] == "BracketLeft" and not r["altgr"], str(r))
r = us(keys.combo("space"))
check("space -> Space", r["code"] == "Space", str(r))
check("a lone modifier can be held", us(keys.combo("Shift_L"))["code"] == "ShiftLeft"
      and keys.label(keys.combo("Control_L")) == "Ctrl")
check("already resolved specs pass through", us({"code": "KeyQ", "ctrl": True})["code"] == "KeyQ")
check("evdev table covers letters and digits",
      all(f"Key{c}" in keys.CODE_TO_EVDEV for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
      and all(f"Digit{d}" in keys.CODE_TO_EVDEV for d in range(10)))

# ---- profiles and icons
check("every profile resolves every shortcut on US",
      all(us(spec) for p in PROFILES for spec in profile_keys(p).values()))
check("every button action has an icon", all(a in ICONS for a in ACTIONS if a != "none"),
      str([a for a in ACTIONS if a != "none" and a not in ICONS]))

# ---- payload
cfg = Config(TMP / "c.json")
cfg["tablet"]["toolbar"]["buttons"] = [
    {"action": "undo"}, {"action": "pan"}, {"action": "eraser"}, {"action": "bogus"},
    {"action": "custom", "keys": keys.combo("b", ctrl=True)}, {"action": "custom"}]
monitors = [{"connector": "eDP-1", "x": 0, "y": 0, "w": 1600, "h": 1000},
            {"connector": "HDMI-1", "x": 1600, "y": 0, "w": 1920, "h": 1080}]
t = payload.build(cfg, us, monitors, "#123456")
kinds = [(b["action"], b["kind"]) for b in t["toolbar"]["buttons"]]
check("buttons get kinds; unknown and empty custom ones are dropped",
      kinds == [("undo", "key"), ("pan", "hold"), ("eraser", "toggle"), ("custom", "key")], str(kinds))
check("custom button label", t["toolbar"]["buttons"][3]["label"].replace(" ", "") in ("Ctrl+B", "Ctrl+b"),
      t["toolbar"]["buttons"][3]["label"])
check("hold button carries its key", t["toolbar"]["buttons"][1]["keys"]["code"] == "Space")
check("desktop accent fills an empty accent", t["toolbar"]["accent"] == "#123456")
check("payload is JSON", bool(json.dumps(t)))
check("saved config is not changed by build()", cfg["tablet"]["toolbar"]["buttons"][0] == {"action": "undo"})

a = payload.area({"monitor": "", "region": payload.FULL, "keep_aspect": False}, monitors)
check("all screens, full area", a == {"display": {"x": 0, "y": 0, "w": 1, "h": 1}, "aspect": 0}, str(a))
a = payload.area({"monitor": "HDMI-1", "region": payload.FULL, "keep_aspect": True}, monitors)
d = a["display"]
check("second monitor maps into the bounding box",
      abs(d["x"] - 1600 / 3520) < 1e-5 and abs(d["w"] - 1920 / 3520) < 1e-5 and abs(d["h"] - 1) < 1e-5, str(a))
check("keep proportions gives the monitor's aspect", abs(a["aspect"] - 1920 / 1080) < 1e-5)
a = payload.area({"monitor": "eDP-1", "region": {"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5}, "keep_aspect": True},
                 monitors)
d = a["display"]
check("region inside a monitor", abs(d["x"] - 800 / 3520) < 1e-5 and abs(d["y"] - 500 / 1080) < 1e-5
      and abs(a["aspect"] - 1.6) < 1e-5, str(a))
a = payload.area({"monitor": "gone", "region": {"x": 0.9, "y": 0, "w": 0.5, "h": 1}}, monitors)
check("unknown monitor falls back to all; region clamped inside",
      abs(a["display"]["x"] + a["display"]["w"] - 1) < 1e-5, str(a))

check("menu labels travel with the payload", bool(t["strings"]["energysaving"] and t["strings"]["title"]))
check("steady taps are on by default", t["tap"]["enabled"] and t["tap"]["radius"] > 0
      and not t["toolbar"]["autohide"])

# ---- config: old files keep working
old = TMP / "old.json"
old.write_text(json.dumps({"profile": "removed-profile", "tablet": {"toolbar": {"side": "right"}}}))
c = Config(old)
check("unknown profile falls back to generic", c["profile"] == "generic")
check("partial config is merged with the defaults",
      c["tablet"]["toolbar"]["side"] == "right" and c["tablet"]["toolbar"]["size"] == DEFAULTS["tablet"]["toolbar"]["size"]
      and c["tablet"]["area"]["monitor"] == "")

# ---- QR
try:
    import cv2
    import numpy as np
except ImportError:
    print("SKIP QR decode (python3-opencv not installed)")
else:
    det = cv2.QRCodeDetector()
    ok = True
    for text in ("http://192.168.1.23:1701/?access_code=Ab3-x_9Q", "x", "http://10.0.0.5:1701/?access_code=" + "k" * 120):
        m = qr.encode(text)
        n = len(m) + 8
        img = np.full((n, n), 255, np.uint8)
        for y, row in enumerate(m):
            for x, v in enumerate(row):
                if v:
                    img[y + 4, x + 4] = 0
        img = cv2.resize(img, (n * 8, n * 8), interpolation=cv2.INTER_NEAREST)
        got = det.detectAndDecode(img)[0]
        ok = ok and got == text
    check("QR codes decode back to the text", ok)
check("QR SVG renders", qr.to_svg(qr.encode("hi")).startswith("<svg"))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
