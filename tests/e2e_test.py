#!/usr/bin/env python3
"""End-to-end test: Inkslate + Weylus + the patched page in headless Chrome.

Starts its own Inkslate instance on spare ports with throwaway config dirs, drives the tablet
page in headless Chrome with synthetic pen/touch/mouse events through the DevTools protocol and
checks what Weylus emits on its uinput devices. The devices are read with `evtest --grab`, so no
test event reaches the desktop (needs passwordless sudo for evtest).

Run from the repository root:  python3 tests/e2e_test.py
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from inkslate import APP_ID, cdp  # noqa: E402

WEB, WS, CTRL, DEVTOOLS = 1811, 9111, 9112, 9333
W, H = 1280, 800
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def weylus_devices():
    """{event_node: name} for the uinput devices Weylus creates."""
    out = {}
    for block in Path("/proc/bus/input/devices").read_text().split("\n\n"):
        name = re.search(r'N: Name="([^"]+)"', block)
        ev = re.search(r"\b(event\d+)\b", block)
        if name and ev and name.group(1).startswith("Weylus"):
            out[ev.group(1)] = name.group(1)
    return out


class Grab:
    """Exclusive evtest reader; text() returns everything since the last mark()."""

    def __init__(self, node, path):
        self.path = path
        self.file = open(path, "w")
        self.proc = subprocess.Popen(["sudo", "-n", "evtest", "--grab", f"/dev/input/{node}"],
                                     stdout=self.file, stderr=subprocess.STDOUT)
        self.offset = 0

    def mark(self):
        self.file.flush()
        self.offset = os.path.getsize(self.path)

    def text(self):
        self.file.flush()
        with open(self.path) as f:
            f.seek(self.offset)
            return f.read()

    def stop(self):
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(3)
        except subprocess.TimeoutExpired:
            subprocess.run(["sudo", "-n", "pkill", "-f", f"evtest --grab {self.path}"])
        self.file.close()


def events(text, code):
    return [int(m.group(1)) for m in re.finditer(rf"\({code}\), value (-?\d+)", text)]


class Page:
    def __init__(self, conn):
        self.c = conn

    def js(self, expr):
        return self.c.evaluate(expr)

    def pen(self, kind, x, y, force=0.0, buttons=1):
        t = {"down": "mousePressed", "move": "mouseMoved", "up": "mouseReleased"}[kind]
        self.c.call("Input.dispatchMouseEvent", type=t, x=x, y=y, pointerType="pen", force=force,
                    button="left" if kind != "move" or buttons else "none",
                    buttons=buttons if kind != "up" else 0, clickCount=1 if kind != "move" else 0)

    def stroke(self, force, x=800, y=400):
        self.pen("move", x - 20, y, buttons=0)   # hover in
        self.pen("down", x, y, force)
        for i in range(1, 6):
            self.pen("move", x + 10 * i, y + 3 * i, force)
        self.pen("up", x + 60, y + 18)

    def mouse_click(self, x, y):
        for t in ("mousePressed", "mouseReleased"):
            self.c.call("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", buttons=1 if t == "mousePressed" else 0,
                        clickCount=1, pointerType="mouse")

    def touch(self, kind, points):
        t = {"start": "touchStart", "move": "touchMove", "end": "touchEnd"}[kind]
        self.c.call("Input.dispatchTouchEvent", type=t,
                    touchPoints=[{"x": x, "y": y, "id": i} for i, (x, y) in enumerate(points)])


def inkslate(env, *args):
    r = subprocess.run([sys.executable, "-m", "inkslate", *args], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise RuntimeError(f"inkslate {args}: {r.stderr}")


def wait_for(fn, timeout=20, step=0.25):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        time.sleep(step)
    return None


def main():
    if not shutil.which("google-chrome") or not shutil.which("evtest"):
        print("needs google-chrome and evtest")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="inkslate-e2e-"))
    env = dict(os.environ, XDG_CONFIG_HOME=str(tmp / "config"), XDG_STATE_HOME=str(tmp / "state"),
               XDG_CACHE_HOME=str(tmp / "cache"), INKSLATE_APP_ID=APP_ID + ".Test")
    (tmp / "config" / "inkslate").mkdir(parents=True)
    (tmp / "config" / "inkslate" / "config.json").write_text(json.dumps({
        "web_port": WEB, "websocket_port": WS, "control_port": CTRL,
        "usb": {"enabled": False}, "profile": "rnote"}))

    before = set(weylus_devices())
    app = subprocess.Popen([sys.executable, "-m", "inkslate", "--background"], cwd=ROOT, env=env,
                           stdout=open(tmp / "app.log", "w"), stderr=subprocess.STDOUT,
                           start_new_session=True)
    chrome = None
    grabs = {}
    try:
        ok = wait_for(lambda: subprocess.run(["curl", "-sf", f"http://127.0.0.1:{WEB}/lib.js"],
                                             capture_output=True).returncode == 0, timeout=30)
        check("weylus serves the patched page", bool(ok))
        if not ok:
            return 1

        chrome = subprocess.Popen(
            ["google-chrome", "--headless=new", f"--remote-debugging-port={DEVTOOLS}",
             f"--user-data-dir={tmp / 'chrome'}", f"--window-size={W},{H}", "--no-first-run",
             "--no-default-browser-check", "--disable-gpu", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        pages = wait_for(lambda: cdp.list_pages(DEVTOOLS))
        conn = cdp.Connection(pages[0]["webSocketDebuggerUrl"], timeout=10)
        page = Page(conn)
        conn.call("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=5)
        conn.call("Page.navigate", url=f"http://127.0.0.1:{WEB}/")

        ready = wait_for(lambda: page.js(
            "!!(window.inkslate && inkslate.state.ctrl && settings && settings.webSocket.readyState === 1)"))
        check("page loads and connects to the control channel", bool(ready))
        js_errors = page.js("document.getElementById('log').textContent")
        check("no errors in the Weylus page log", not js_errors.strip(), js_errors.strip()[:200])
        if not ready:
            return 1
        time.sleep(1.5)  # ConfigOk -> PointerHandler

        new = {}
        found = wait_for(lambda: {n: name for n, name in weylus_devices().items() if n not in before}
                         if len(set(weylus_devices()) - before) >= 3 else None, timeout=10)
        new = found or {}
        by_name = {name: node for node, name in new.items()}
        check("weylus created its uinput devices", {"Weylus Stylus", "Weylus Keyboard", "Weylus Touch"} <= set(by_name),
              str(by_name))
        for name in ("Weylus Stylus", "Weylus Keyboard", "Weylus Touch", "Weylus Mouse"):
            if name in by_name:
                grabs[name] = Grab(by_name[name], str(tmp / (name.split()[1].lower() + ".log")))
        time.sleep(1)
        stylus, keyboard, touch = grabs["Weylus Stylus"], grabs["Weylus Keyboard"], grabs["Weylus Touch"]
        mouse = grabs["Weylus Mouse"]

        # ---- pressure curve: identity, then "full pressure at 0.5"
        stylus.mark()
        page.stroke(0.25)
        time.sleep(0.5)
        p = events(stylus.text(), "ABS_PRESSURE")
        check("identity curve keeps pressure", bool(p) and abs(max(p) - 0.25 * 65535) < 700, f"max={max(p) if p else None}")

        inkslate(env, "--set", "tablet.curve.in_max=0.5")
        wait_for(lambda: page.js("inkslate.config().curve.in_max") == 0.5, timeout=5)
        stylus.mark()
        page.stroke(0.25)
        time.sleep(0.5)
        p = events(stylus.text(), "ABS_PRESSURE")
        check("curve in_max=0.5 doubles pressure", bool(p) and abs(max(p) - 0.5 * 65535) < 700, f"max={max(p) if p else None}")
        inkslate(env, "--set", "tablet.curve.in_max=1.0")

        # ---- eraser mode -> BTN_TOOL_RUBBER
        inkslate(env, "--set", "tablet.eraser=true")
        wait_for(lambda: page.js("inkslate.config().eraser") is True, timeout=5)
        stylus.mark()
        page.stroke(0.4)
        time.sleep(0.5)
        t = stylus.text()
        check("eraser mode sends the rubber tool", 1 in events(t, "BTN_TOOL_RUBBER") and 1 not in events(t, "BTN_TOOL_PEN"),
              f"rubber={events(t, 'BTN_TOOL_RUBBER')} pen={events(t, 'BTN_TOOL_PEN')}")
        inkslate(env, "--set", "tablet.eraser=false")
        wait_for(lambda: page.js("inkslate.config().eraser") is False, timeout=5)
        stylus.mark()
        page.stroke(0.4)
        time.sleep(0.5)
        check("pen tool is back after eraser off", 1 in events(stylus.text(), "BTN_TOOL_PEN"))

        # ---- toolbar: undo button sends Ctrl+Z, redo sends Ctrl+Shift+Z (Rnote profile)
        rects = page.js("""JSON.stringify(Array.from(document.querySelectorAll('#inkslate-bar button'))
            .map(b => { const r = b.getBoundingClientRect(); return [b.dataset.action, r.x + r.width / 2, r.y + r.height / 2]; }))""")
        rects = json.loads(rects)
        check("toolbar shows undo, redo, eraser", [r[0] for r in rects] == ["undo", "redo", "eraser"], str(rects))
        pos = {a: (x, y) for a, x, y in rects}
        time.sleep(0.8)
        keyboard.mark()
        mouse.mark()
        page.mouse_click(*pos["undo"])
        time.sleep(0.4)
        t = keyboard.text()
        check("toolbar undo -> Ctrl+Z", 1 in events(t, "KEY_LEFTCTRL") and 1 in events(t, "KEY_Z")
              and 1 not in events(t, "KEY_LEFTSHIFT"))
        keyboard.mark()
        page.mouse_click(*pos["redo"])
        time.sleep(0.4)
        t = keyboard.text()
        check("toolbar redo -> Ctrl+Shift+Z", 1 in events(t, "KEY_LEFTSHIFT") and 1 in events(t, "KEY_Z"))
        stylus.mark()
        page.mouse_click(*pos["eraser"])
        time.sleep(0.3)
        check("toolbar eraser tap toggles eraser", page.js("inkslate.config().eraser") is True)
        page.mouse_click(*pos["eraser"])
        time.sleep(0.3)
        check("toolbar taps don't reach Weylus as pen/mouse input",
              "ABS_X" not in stylus.text() and "EV_" not in mouse.text().replace("EV_SYN", ""), mouse.text()[:120])

        inkslate(env, "--set", "profile=xournalpp")
        wait_for(lambda: page.js("inkslate.config().keys.redo.code") == "KeyY", timeout=5)
        keyboard.mark()
        page.mouse_click(*pos["redo"])
        time.sleep(0.4)
        check("Xournal++ profile: redo -> Ctrl+Y", 1 in events(keyboard.text(), "KEY_Y"))

        # ---- gestures with finger input off
        inkslate(env, "--native", "enable_touch=false")
        wait_for(lambda: page.js("settings.checks.get('enable_touch').checked") is False, timeout=5)
        time.sleep(1.0)   # outside the palm window of the pen strokes above
        keyboard.mark()
        touch.mark()
        page.touch("start", [(600, 300), (700, 320)])
        time.sleep(0.05)
        page.touch("end", [])
        time.sleep(0.5)
        check("two-finger tap -> undo", 1 in events(keyboard.text(), "KEY_Z"))
        check("finger input off: no touch reaches Weylus", "ABS_MT" not in touch.text())
        keyboard.mark()
        page.touch("start", [(600, 300), (700, 320), (800, 340)])
        time.sleep(0.05)
        page.touch("end", [])
        time.sleep(0.5)
        check("three-finger tap -> redo (Ctrl+Y)", 1 in events(keyboard.text(), "KEY_Y"))
        keyboard.mark()
        page.pen("move", 500, 500, buttons=0)    # pen near the screen
        page.touch("start", [(600, 300), (700, 320)])
        time.sleep(0.05)
        page.touch("end", [])
        time.sleep(0.5)
        check("no gesture while the pen is near (resting palm)", "KEY_Z" not in keyboard.text())

        # ---- palm rejection with finger input on
        inkslate(env, "--native", "enable_touch=true")
        wait_for(lambda: page.js("settings.checks.get('enable_touch').checked") is True, timeout=5)
        time.sleep(1.0)
        touch.mark()
        page.pen("move", 500, 500, buttons=0)
        page.touch("start", [(600, 300)])
        page.touch("move", [(620, 310)])
        page.touch("end", [])
        time.sleep(0.5)
        check("palm rejection: touch right after pen is ignored", "ABS_MT_TRACKING_ID" not in touch.text())
        time.sleep(1.0)
        touch.mark()
        page.touch("start", [(600, 300)])
        page.touch("move", [(620, 310)])
        page.touch("end", [])
        time.sleep(0.5)
        check("touch goes through when the pen is away", "ABS_MT_TRACKING_ID" in touch.text())

        # ---- energy saving stays black after a resize (the fullscreen bug)
        inkslate(env, "--native", "energysaving=true")
        wait_for(lambda: page.js("settings.checks.get('energysaving').checked") is True, timeout=5)
        conn.call("Emulation.setDeviceMetricsOverride", width=1000, height=700, deviceScaleFactor=1, mobile=False)
        time.sleep(0.6)
        px = page.js("""(() => { const c = document.getElementById('canvas'); const x = c.getContext('2d');
            return x ? Array.from(x.getImageData(c.width >> 1, c.height >> 1, 1, 1).data) : null; })()""")
        check("energy saving screen is still black after resize", px == [0, 0, 0, 255], str(px))
        conn.call("Emulation.clearDeviceMetricsOverride")
        inkslate(env, "--native", "energysaving=false")

        # ---- control channel refuses other origins
        try:
            import socket
            s = socket.create_connection(("127.0.0.1", CTRL), timeout=3)
            s.sendall(("GET /control HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\n"
                       "Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                       "Sec-WebSocket-Version: 13\r\nOrigin: http://evil.example\r\n\r\n").encode())
            data = b""
            end = time.monotonic() + 3
            while time.monotonic() < end and b"\x88" not in data:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            check("control channel closes foreign origins", b"\x88" in data or not data)
            s.close()
        except OSError as e:
            check("control channel closes foreign origins", True, f"refused: {e}")

        conn.close()
    finally:
        for g in grabs.values():
            g.stop()
        if chrome:
            chrome.terminate()
        subprocess.run([sys.executable, "-m", "inkslate", "--quit"], cwd=ROOT, env=env,
                       capture_output=True, timeout=20)
        try:
            app.wait(10)
        except subprocess.TimeoutExpired:
            os.killpg(app.pid, signal.SIGKILL)
        leftover = subprocess.run(["pgrep", "-f", f"--web-port {WEB}"], capture_output=True, text=True).stdout
        check("quit stops the Weylus backend", not leftover.strip(), leftover.strip())
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
