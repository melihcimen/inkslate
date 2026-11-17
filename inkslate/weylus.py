"""Runs Weylus headless (--no-gui) as Inkslate's backend and builds the patched tablet page."""

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from gi.repository import GLib

from . import __version__
from .config import CACHE_DIR, STATE_DIR

WEB_DIR = Path(__file__).resolve().parent / "web"


def find_weylus(configured=""):
    candidates = [configured] if configured else []
    candidates += [shutil.which("weylus") or "", str(Path.home() / ".local" / "bin" / "weylus")]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def build_lib_js(weylus, control_port):
    """Weylus' own lib.js with Inkslate's extension appended (written to the cache dir)."""
    base = subprocess.run([weylus, "--print-lib-js"], capture_output=True, text=True,
                          check=True, timeout=15).stdout
    ext = (WEB_DIR / "inkslate.js").read_text()
    ext = ext.replace("@CONTROL_PORT@", str(int(control_port))).replace("@VERSION@", __version__)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / "lib.js"
    out.write_text(base + "\n" + ext)
    return out


class WeylusProcess:
    """Starts, watches and restarts the Weylus server. Callbacks run in the GLib main loop."""

    def __init__(self, config, on_state):
        self.config = config
        self.on_state = on_state      # on_state(running: bool, message: str)
        self.proc = None
        self.wanted = False
        self._restarts = []
        self._watch = None
        self.log_path = STATE_DIR / "weylus.log"

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def command(self, weylus, lib_js):
        c = self.config
        bind = "127.0.0.1" if c["mode"] == "usb" else "0.0.0.0"
        cmd = [weylus, "--no-gui", "--bind-address", bind,
               "--web-port", str(c["web_port"]), "--websocket-port", str(c["websocket_port"]),
               "--custom-lib-js", str(lib_js)]
        if c["mode"] == "wifi":
            cmd += ["--access-code", c["access_code"]]
        if c["video"]["wayland_support"]:
            cmd.append("--wayland-support")
        if c["video"]["try_vaapi"]:
            cmd.append("--try-vaapi")
        if c["video"]["try_nvenc"]:
            cmd.append("--try-nvenc")
        return cmd

    def start(self):
        self.wanted = True
        if self.running:
            return
        weylus = find_weylus(self.config["weylus_path"])
        if not weylus:
            self.on_state(False, "weylus-missing")
            return
        try:
            lib_js = build_lib_js(weylus, self.config["control_port"])
        except (OSError, subprocess.SubprocessError) as e:
            self.on_state(False, f"lib.js: {e}")
            return
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        log = open(self.log_path, "ab")
        log.write(f"\n--- {time.ctime()} {' '.join(self.command(weylus, lib_js))}\n".encode())
        log.flush()
        self.proc = subprocess.Popen(self.command(weylus, lib_js), stdout=log, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, start_new_session=True)
        log.close()
        # Poll instead of GLib.child_watch_add: Popen.poll() and a GLib child watch would race
        # to reap the same pid.
        if self._watch is None:
            self._watch = GLib.timeout_add(500, self._poll)
        self.on_state(True, "")

    def stop(self):
        self.wanted = False
        if self.running:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None
        self.on_state(False, "")

    def restart(self):
        self.stop()
        self.start()

    def _poll(self):
        if self.proc is None or self.proc.poll() is None:
            return True
        self.proc = None
        if not self.wanted:
            return True
        # Restart after a crash, but give up when it keeps dying (e.g. port already in use).
        now = time.monotonic()
        self._restarts = [t for t in self._restarts if now - t < 60] + [now]
        if len(self._restarts) > 3:
            self.on_state(False, "crashed")
            return True
        self.on_state(False, "restarting")
        GLib.timeout_add_seconds(2, self._delayed_start)
        return True

    def _delayed_start(self):
        if self.wanted:
            self.start()
        return False

    def tail_log(self, lines=20):
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return ""
