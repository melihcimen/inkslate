#!/usr/bin/env python3
"""Renders every page of the main window to PNG (for the README and for reviewing the UI).

Runs a separate instance (own app id, throwaway config, spare ports, USB off).
Usage: python3 tests/screenshots.py OUTDIR [--dark] [--width 720] [--wifi] [--set '{"tablet": ...}']
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ap = argparse.ArgumentParser()
ap.add_argument("outdir")
ap.add_argument("--dark", action="store_true")
ap.add_argument("--width", type=int, default=720)
ap.add_argument("--height", type=int, default=820)
ap.add_argument("--wifi", action="store_true", help="Wi-Fi mode (shows the QR code)")
ap.add_argument("--set", default="{}", help="JSON merged into the config")
ap.add_argument("--pages", default="status,tablet,pen,area,toolbar,connection")
ap.add_argument("--bottom", action="store_true", help="scroll each page to the end")
args = ap.parse_args()

tmp = Path(tempfile.mkdtemp(prefix="inkslate-shots-"))
(tmp / "config" / "inkslate").mkdir(parents=True)
conf = {"web_port": 1821, "websocket_port": 9121, "control_port": 9122, "usb": {"enabled": False},
        "access_code": "demo-code", "mode": "wifi" if args.wifi else "usb"}
conf.update(json.loads(args.set))
(tmp / "config" / "inkslate" / "config.json").write_text(json.dumps(conf))
os.environ.update(XDG_CONFIG_HOME=str(tmp / "config"), XDG_STATE_HOME=str(tmp / "state"),
                  XDG_CACHE_HOME=str(tmp / "cache"), INKSLATE_APP_ID="io.github.melihcimen.Inkslate.Shots")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, GLib, Graphene, Gtk  # noqa: E402

import inkslate.app  # noqa: E402
from inkslate.app import InkslateApp  # noqa: E402

# Screenshots end up in the README: don't show this machine's address or home directory.
inkslate.app.lan_ip = lambda: "192.168.1.23"
InkslateApp.weylus_description = lambda self: "/usr/bin/weylus"

PAGES = args.pages.split(",")
out = Path(args.outdir)
out.mkdir(parents=True, exist_ok=True)


class ShotApp(InkslateApp):
    def do_activate(self):
        InkslateApp.do_activate(self)
        if args.dark:
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        self.window.set_default_size(args.width, args.height)
        # fake a pressure sample so the curve preview shows the live dot
        GLib.timeout_add(800, self._shoot, 0)

    def _shoot(self, i):
        if i >= len(PAGES):
            self.quit()
            return False
        self.window.stack.set_visible_child_name(PAGES[i])
        if args.bottom:
            page = self.window.stack.get_visible_child()
            child = page.get_first_child()
            while child is not None and not isinstance(child, Gtk.ScrolledWindow):
                child = child.get_first_child()
            if child is not None:
                GLib.timeout_add(300, lambda adj=child.get_vadjustment(): adj.set_value(adj.get_upper()) or False)
        if PAGES[i] == "pen":
            self.window.curve_view.peak_raw = 0.84
            self.window.push_pressure({"raw": 0.53, "out": 0.53, "contact": True})
        GLib.timeout_add(900, self._snap, i)
        return False

    def _snap(self, i):
        win = self.window
        w, h = win.get_width(), win.get_height()
        paintable = Gtk.WidgetPaintable.new(win)
        snap = Gtk.Snapshot()
        paintable.snapshot(snap, w, h)
        node = snap.to_node()
        if node is not None:
            rect = Graphene.Rect().init(0, 0, w, h)
            tex = win.get_renderer().render_texture(node, rect)
            tex.save_to_png(str(out / f"{i + 1}-{PAGES[i]}.png"))
            print("saved", out / f"{i + 1}-{PAGES[i]}.png")
        GLib.timeout_add(200, self._shoot, i + 1)
        return False


sys.exit(ShotApp().run([sys.argv[0]]))
