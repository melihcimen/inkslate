#!/usr/bin/env python3
"""Tests the USB manager against a fake `adb` (no tablet needed).

Covers: detection, port reversing, opening the page, replug, battery, dimming and restoring the
brightness, and the fullscreen error path. Run: python3 tests/usb_test.py
"""

import http.server
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="inkslate-usb-test-"))
for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = str(TMP / var.lower())
BIN = TMP / "bin"
BIN.mkdir()
STATE = TMP / "adb-state.json"
CALLS = TMP / "adb-calls.log"
os.environ["PATH"] = f"{BIN}:{os.environ['PATH']}"

FAKE_ADB = r'''#!/usr/bin/env python3
import json, sys
state_file, calls = sys.argv[0] + ".state", sys.argv[0] + ".calls"
args = sys.argv[1:]
st = json.load(open(STATE))
open(CALLS, "a").write(json.dumps(args) + "\n")
serial = None
if args[:1] == ["-s"]:
    serial, args = args[1], args[2:]
def save():
    json.dump(st, open(STATE, "w"))
if args == ["start-server"]:
    pass
elif args == ["devices"]:
    print("List of devices attached")
    for s, kind in st["devices"].items():
        print(f"{s}\t{kind}")
elif args == ["reverse", "--list"]:
    for r in st["reverse"]:
        print(f"UsbFfs {r} {r}")
elif args[0] == "reverse":
    st["reverse"].append(args[1]); save()
elif args[:2] == ["shell", "getprop"]:
    print("TESTPAD")
elif args[:3] == ["shell", "dumpsys", "battery"]:
    print("Current Battery Service state:\n  status: 2\n  level: 55")
elif args[:3] == ["shell", "settings", "get"]:
    print(st["settings"].get(args[4], "null"))
elif args[:3] == ["shell", "settings", "put"]:
    st["settings"][args[4]] = args[5]; save()
elif args[:3] == ["shell", "am", "start"]:
    print("Starting: Intent { act=android.intent.action.VIEW }")
elif args[0] == "forward":
    print("error: no devtools", file=sys.stderr); sys.exit(1)
'''.replace("STATE", repr(str(STATE))).replace("CALLS", repr(str(CALLS)))

(BIN / "adb").write_text(FAKE_ADB)
(BIN / "adb").chmod(0o755)
STATE.write_text(json.dumps({"devices": {"TAB1": "device"}, "reverse": [],
                             "settings": {"screen_brightness": "140", "screen_brightness_mode": "1"}}))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gi  # noqa: E402

from gi.repository import GLib  # noqa: E402

from inkslate import usb  # noqa: E402
from inkslate.config import Config  # noqa: E402

WEB_PORT = 18731


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"// lib.js")

    def log_message(self, *a):
        pass


server = http.server.HTTPServer(("127.0.0.1", WEB_PORT), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

config = Config()
config["web_port"] = WEB_PORT
config["websocket_port"] = WEB_PORT + 1
config["control_port"] = WEB_PORT + 2
usb.UsbManager.POLL_SECONDS = 0.5

events, statuses = [], []
mgr = usb.UsbManager(config, statuses.append, lambda name, **info: events.append((name, info)))
loop = GLib.MainLoop()
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def calls():
    return [json.loads(line) for line in CALLS.read_text().splitlines()] if CALLS.exists() else []


def wait_for(pred, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        ctx = loop.get_context()
        while ctx.pending():
            ctx.iteration(False)
        try:
            if pred():
                return True
        except ValueError:      # the fake adb was writing its state file just then
            pass
        time.sleep(0.05)
    return False


def adb_state(**changes):
    st = json.loads(STATE.read_text())
    st.update(changes)
    STATE.write_text(json.dumps(st))


def am_starts():
    return [c for c in calls() if c[2:5] == ["shell", "am", "start"]]


mgr.start()
check("tablet detected", wait_for(lambda: mgr.status["state"] == "connected"),
      str(mgr.status))
check("model and battery read", wait_for(lambda: statuses and statuses[-1].get("battery") == 55
                                         and statuses[-1]["charging"] and statuses[-1]["model"] == "TESTPAD"),
      str(statuses[-1] if statuses else None))
check("connected event (newly)", wait_for(lambda: ("connected", {"newly": True}) in events), str(events))
ports = {f"tcp:{p}" for p in (WEB_PORT, WEB_PORT + 1, WEB_PORT + 2)}
check("all three ports reversed", wait_for(lambda: set(json.loads(STATE.read_text())["reverse"]) == ports))
check("page opened in Chrome with the inkslate tab id",
      wait_for(lambda: any("inkslate" in c and f"http://127.0.0.1:{WEB_PORT}/" in c for c in am_starts())),
      str(am_starts()))
n_open = len(am_starts())

time.sleep(1.5)
check("no reopen while nothing changes", len(am_starts()) == n_open, f"{len(am_starts())} vs {n_open}")

adb_state(devices={})
check("unplug -> disconnected", wait_for(lambda: ("disconnected", {}) in events
                                          and mgr.status["state"] == "none"))
adb_state(devices={"TAB1": "unauthorized"})
check("unauthorized state shown", wait_for(lambda: mgr.status["state"] == "unauthorized"))
adb_state(devices={"TAB1": "device"}, reverse=[])
check("replug -> page opened again", wait_for(lambda: len(am_starts()) > n_open, 15))

mgr.request("dim", level=10)
check("dim sets manual low brightness",
      wait_for(lambda: json.loads(STATE.read_text())["settings"] == {"screen_brightness": "26",
                                                                       "screen_brightness_mode": "0"}),
      str(json.loads(STATE.read_text())["settings"]))
check("original brightness remembered", usb.UsbManager.dimmed())
mgr.request("undim")
check("undim restores brightness and auto mode",
      wait_for(lambda: json.loads(STATE.read_text())["settings"] == {"screen_brightness": "140",
                                                                       "screen_brightness_mode": "1"}),
      str(json.loads(STATE.read_text())["settings"]))
check("nothing left to restore", wait_for(lambda: not usb.UsbManager.dimmed()))

mgr.request("fullscreen")
check("fullscreen failure is reported",
      wait_for(lambda: any(n == "fullscreen" and not i.get("ok") for n, i in events)), str(events[-1:]))

mgr.stop()
server.shutdown()
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
