"""USB tablet handling through adb (worker thread): port reversing, opening the page,
fullscreen via Chrome DevTools, brightness and battery."""

import json
import queue
import re
import shutil
import socket
import subprocess
import threading
import time

from . import cdp
from .config import STATE_DIR
from .control import idle

BRIGHTNESS_FILE = STATE_DIR / "tablet-brightness.json"

FULLSCREEN_JS = """(async () => {
    if (typeof settings === "undefined" || !settings) return "not-ready";
    if (!document.fullscreenElement)
        await document.body.requestFullscreen({ navigationUI: "hide" });
    return "ok";
})()"""


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class UsbManager:
    POLL_SECONDS = 2

    def __init__(self, config, on_status, on_event):
        """on_status(dict) and on_event(name, **info) are called in the GLib main loop."""
        self.config = config
        self.on_status = on_status
        self.on_event = on_event
        self.adb = shutil.which("adb")
        self.status = {"adb": bool(self.adb), "state": "none", "serial": None, "model": None,
                       "battery": None, "charging": False}
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        self._models = {}
        self._last_battery = 0

    # ------------------------------------------------------------------ public (main loop)

    def start(self):
        if self._thread or not self.adb or not self.config["usb"].get("enabled", True):
            idle(self.on_status, dict(self.status))
            return
        self._thread = threading.Thread(target=self._run, name="inkslate-usb", daemon=True)
        self._thread.start()

    def stop(self, timeout=5):
        self._stop.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout)
            self._thread = None

    def request(self, action, **kw):
        """Queue an action: open_page, fullscreen, dim, undim, reverse."""
        self._queue.put((action, kw))

    @property
    def connected(self):
        return self.status["state"] == "connected"

    # ------------------------------------------------------------------ worker

    def _adb(self, *args, serial=None, timeout=10):
        cmd = [self.adb] + (["-s", serial] if serial else []) + list(args)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return r.returncode, r.stdout.strip()
        except (OSError, subprocess.SubprocessError) as e:
            return -1, str(e)

    def _run(self):
        self._adb("start-server", timeout=20)
        while not self._stop.is_set():
            try:
                self._poll()
            except Exception as e:
                print(f"inkslate: usb poll failed: {e!r}")
            deadline = time.monotonic() + self.POLL_SECONDS
            while not self._stop.is_set():
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                try:
                    item = self._queue.get(timeout=left)
                except queue.Empty:
                    break
                if item is None:
                    return
                action, kw = item
                try:
                    getattr(self, "_do_" + action)(**kw)
                except Exception as e:
                    print(f"inkslate: usb action {action} failed: {e!r}")

    def _set_status(self, **changes):
        changed = any(self.status.get(k) != v for k, v in changes.items())
        self.status.update(changes)
        if changed:
            idle(self.on_status, dict(self.status))

    def _poll(self):
        rc, out = self._adb("devices")
        devices = {}
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                devices[parts[0]] = parts[1]
        serial = next((s for s, st in devices.items() if st == "device"), None)
        if serial is None:
            state = "unauthorized" if "unauthorized" in devices.values() else (
                "offline" if devices else "none")
            if self.status["state"] == "connected":
                idle(self.on_event, "disconnected")
            self._set_status(state=state, serial=None, model=None, battery=None, charging=False)
            return

        newly = self.status["state"] != "connected" or self.status["serial"] != serial
        if serial not in self._models:
            _, model = self._adb("shell", "getprop", "ro.product.model", serial=serial)
            self._models[serial] = model or serial
        self._set_status(state="connected", serial=serial, model=self._models[serial])

        if self._ensure_reverse(serial) or newly:
            idle(self.on_event, "connected", newly=newly)
            if self.config["mode"] == "usb" and self.config["usb"]["auto_open"]:
                self._do_open_page()
        if time.monotonic() - self._last_battery > 60 or newly:
            self._read_battery(serial)

    def _ports(self):
        c = self.config
        return [c["web_port"], c["websocket_port"], c["control_port"]]

    def _ensure_reverse(self, serial):
        """Returns True if the reverse forwards had to be (re)created (e.g. cable replugged)."""
        _, out = self._adb("reverse", "--list", serial=serial)
        missing = [p for p in self._ports() if f"tcp:{p} tcp:{p}" not in out]
        if not missing:
            return False
        for p in missing:
            self._adb("reverse", f"tcp:{p}", f"tcp:{p}", serial=serial)
        return True

    def _do_reverse(self):
        if self.status["serial"]:
            self._ensure_reverse(self.status["serial"])

    def _read_battery(self, serial):
        self._last_battery = time.monotonic()
        rc, out = self._adb("shell", "dumpsys", "battery", serial=serial)
        level = re.search(r"^\s*level:\s*(\d+)", out, re.M)
        status = re.search(r"^\s*status:\s*(\d+)", out, re.M)
        self._set_status(battery=int(level.group(1)) if level else None,
                         charging=bool(status and status.group(1) in ("2", "5")))

    def _do_open_page(self):
        serial = self.status["serial"]
        if not serial:
            return
        url = f"http://127.0.0.1:{self.config['web_port']}/"
        self._adb("shell", "input", "keyevent", "KEYCODE_WAKEUP", serial=serial)
        # application_id makes Chrome reuse the tab it opened for us instead of adding a new one.
        rc, out = self._adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url,
                            "-e", "com.android.browser.application_id", "inkslate",
                            "com.android.chrome", serial=serial)
        if rc != 0 or "Error" in out:
            self._adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url,
                      serial=serial)

    def _do_fullscreen(self, attempts=8):
        serial = self.status["serial"]
        if not serial:
            return
        port = _free_port()
        rc, out = self._adb("forward", f"tcp:{port}", "localabstract:chrome_devtools_remote",
                            serial=serial)
        if rc != 0:
            idle(self.on_event, "fullscreen", ok=False, reason="devtools")
            return
        prefix = f"http://127.0.0.1:{self.config['web_port']}/"
        result = "no-page"
        try:
            for _ in range(attempts):
                try:
                    for page in cdp.list_pages(port):
                        if not page.get("url", "").startswith(prefix):
                            continue
                        try:
                            with cdp.Connection(page["webSocketDebuggerUrl"], timeout=3) as c:
                                if c.evaluate("document.visibilityState") != "visible":
                                    continue
                                result = c.evaluate(FULLSCREEN_JS, user_gesture=True)
                        except (OSError, cdp.CDPError) as e:
                            result = str(e)
                            continue
                        if result == "ok":
                            idle(self.on_event, "fullscreen", ok=True)
                            return
                except OSError as e:
                    result = str(e)
                time.sleep(1)
        finally:
            self._adb("forward", "--remove", f"tcp:{port}", serial=serial)
        idle(self.on_event, "fullscreen", ok=False, reason=result)

    def _do_dim(self, level):
        serial = self.status["serial"]
        if not serial:
            return
        if not BRIGHTNESS_FILE.exists():
            _, mode = self._adb("shell", "settings", "get", "system", "screen_brightness_mode",
                                serial=serial)
            _, value = self._adb("shell", "settings", "get", "system", "screen_brightness",
                                 serial=serial)
            if not value.isdigit():
                return
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            BRIGHTNESS_FILE.write_text(json.dumps({"serial": serial, "mode": mode, "value": value}))
        target = max(1, round(255 * level / 100))
        self._adb("shell", "settings", "put", "system", "screen_brightness_mode", "0", serial=serial)
        self._adb("shell", "settings", "put", "system", "screen_brightness", str(target), serial=serial)

    def _do_undim(self):
        serial = self.status["serial"]
        if not serial or not BRIGHTNESS_FILE.exists():
            return
        try:
            saved = json.loads(BRIGHTNESS_FILE.read_text())
        except (OSError, ValueError):
            BRIGHTNESS_FILE.unlink(missing_ok=True)
            return
        if saved.get("serial") != serial:
            return
        self._adb("shell", "settings", "put", "system", "screen_brightness", saved["value"], serial=serial)
        if saved.get("mode", "").isdigit():
            self._adb("shell", "settings", "put", "system", "screen_brightness_mode", saved["mode"],
                      serial=serial)
        BRIGHTNESS_FILE.unlink(missing_ok=True)

    @staticmethod
    def dimmed():
        return BRIGHTNESS_FILE.exists()
