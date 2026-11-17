"""Runs Weylus headless (--no-gui) as Inkslate's backend and builds the patched tablet page.

Works with the official Weylus 0.11.4 release and with Weylus Community Edition (builds of Weylus
master), as a binary or as the Flatpak from Flathub.
"""

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from gi.repository import GLib

from . import __version__
from .config import CACHE_DIR, STATE_DIR
from .icons import ICONS

WEB_DIR = Path(__file__).resolve().parent / "web"


FLATPAK_ID = "io.github.electronstudio.WeylusCommunityEdition"


def _flatpak_installed():
    if not shutil.which("flatpak"):
        return False
    r = subprocess.run(["flatpak", "info", FLATPAK_ID], capture_output=True, timeout=10)
    return r.returncode == 0


def find_weylus(configured=""):
    """The command prefix that runs Weylus, e.g. ["/usr/bin/weylus"], or None.

    `configured` may be a path or "flatpak" for the Weylus Community Edition Flatpak."""
    if configured == "flatpak":
        return flatpak_command() if _flatpak_installed() else None
    candidates = [configured] if configured else []
    candidates += [shutil.which("weylus") or "", str(Path.home() / ".local" / "bin" / "weylus")]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return [c]
    if not configured and _flatpak_installed():
        return flatpak_command()
    return None


def flatpak_command():
    # The sandbox can't read our files; expose just the cache dir with the patched lib.js.
    return ["flatpak", "run", f"--filesystem={CACHE_DIR}:ro", "--command=weylus", FLATPAK_ID]


def describe(cmd):
    if not cmd:
        return ""
    return "Flatpak: " + FLATPAK_ID if cmd[0] == "flatpak" else cmd[0]


def detect_flavor(lib_js):
    """"ce" for Weylus master / Community Edition (websocket on the web port, frame rate setting),
    "0.11" for the 0.11.x releases."""
    return "ce" if ('"/ws"' in lib_js or "frame_rate_input" in lib_js) else "0.11"


def build_lib_js(weylus_cmd, control_port):
    """Weylus' own lib.js with Inkslate's extension appended (written to the cache dir)."""
    base = subprocess.run(list(weylus_cmd) + ["--print-lib-js"], capture_output=True, text=True,
                          check=True, timeout=30).stdout
    ext = (WEB_DIR / "inkslate.js").read_text()
    ext = (ext.replace("@CONTROL_PORT@", str(int(control_port)))
              .replace("@VERSION@", __version__)
              .replace("/*@ICONS@*/ {}", json.dumps(ICONS)))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / "lib.js"
    out.write_text(base + "\n" + ext)
    return out, detect_flavor(base)


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
        self.flavor = None            # "0.11" or "ce", known after the first start
        self.cmd = None

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def command(self, weylus_cmd, lib_js, flavor="0.11"):
        c = self.config
        bind = "127.0.0.1" if c["mode"] == "usb" else "0.0.0.0"
        cmd = list(weylus_cmd) + ["--no-gui", "--bind-address", bind, "--web-port", str(c["web_port"])]
        if flavor == "0.11":      # newer Weylus serves the websocket on the web port
            cmd += ["--websocket-port", str(c["websocket_port"])]
        cmd += ["--custom-lib-js", str(lib_js)]
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
        self.cmd = weylus
        if not weylus:
            self.on_state(False, "weylus-missing")
            return
        try:
            lib_js, self.flavor = build_lib_js(weylus, self.config["control_port"])
        except (OSError, subprocess.SubprocessError) as e:
            self.on_state(False, f"lib.js: {e}")
            return
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        cmd = self.command(weylus, lib_js, self.flavor)
        log = open(self.log_path, "ab")
        log.write(f"\n--- {time.ctime()} {' '.join(cmd)}\n".encode())
        log.flush()
        self.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
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
            # The whole process group: `flatpak run` keeps the sandboxed Weylus as a child.
            self._signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._signal(signal.SIGKILL)
                self.proc.wait()
        self.proc = None
        self.on_state(False, "")

    def _signal(self, sig):
        try:
            os.killpg(self.proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

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
