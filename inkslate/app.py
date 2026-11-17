"""Application: owns the config, the Weylus process, the control channel and the USB manager."""

import json
import logging
import logging.handlers
import os
import socket
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, __version__  # noqa: E402
from .config import STATE_DIR, Config  # noqa: E402
from .control import ControlServer  # noqa: E402
from .i18n import _  # noqa: E402
from .usb import UsbManager  # noqa: E402
from .weylus import WeylusProcess, find_weylus  # noqa: E402

AUTOSTART_FILE = Path(GLib.get_user_config_dir()) / "autostart" / f"{APP_ID}.desktop"
log = logging.getLogger("inkslate")


def setup_logging():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(STATE_DIR / "inkslate.log", maxBytes=512 * 1024,
                                                   backupCount=1)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.addHandler(logging.StreamHandler())
    log.setLevel(logging.INFO)


class InkslateApp(Adw.Application):
    def __init__(self):
        # INKSLATE_APP_ID lets tests run a separate instance next to the real one.
        super().__init__(application_id=os.environ.get("INKSLATE_APP_ID", APP_ID),
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("background", ord("b"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _("Start without opening the window"), None)
        self.add_main_option("quit", ord("q"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _("Stop the running instance"), None)
        self.add_main_option("version", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _("Print the version"), None)
        self.add_main_option("status", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _("Print the state of the running instance"), None)
        self.add_main_option("set", ord("s"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING_ARRAY,
                             _("Change a setting of the running instance, e.g. tablet.eraser=true"),
                             "PATH=JSON")
        self.add_main_option("native", ord("n"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING_ARRAY,
                             _("Change a Weylus setting on the tablet page, e.g. energysaving=true"),
                             "KEY=JSON")
        self.add_main_option("action", ord("a"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             _("Run a tablet action: open-page, fullscreen, reload, undo, redo, eraser"),
                             "ACTION")
        self.window = None
        self.config = None
        self.weylus_message = ""
        self._monitor = False
        self._hid_once = False

    # ------------------------------------------------------------------ lifecycle

    def do_startup(self):
        Adw.Application.do_startup(self)
        setup_logging()
        log.info("Inkslate %s starting", __version__)
        self.config = Config()
        self.config.save()   # writes a generated access code / new defaults back
        self.weylus = WeylusProcess(self.config, self._on_weylus_state)
        self.control = ControlServer(self.config, {
            "hello": self._on_hello,
            "state": self._on_tablet_state,
            "pressure": self._on_pressure,
            "closed": self._on_tablet_closed,
        })
        self.usb = UsbManager(self.config, self._on_usb_status, self._on_usb_event)

        for name, cb in (("about", self._show_about), ("quit", self.quit)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, cb=cb: cb())
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("window.close", ["<Control>w"])

        # The app keeps running (and serving the tablet) with the window closed.
        self.hold()
        self._start_backend()

    def _start_backend(self):
        try:
            self.control.start()
        except GLib.Error as e:
            self.weylus_message = _("Control port {port} is busy: {err}").format(
                port=self.config["control_port"], err=e.message)
        self.weylus.start()
        self.usb.start()

    def do_command_line(self, command_line):
        opts = command_line.get_options_dict().end().unpack()
        if opts.get("version"):
            command_line.print_literal(f"Inkslate {__version__}\n")
            return 0
        if opts.get("quit"):
            self.quit()
            return 0
        if opts.get("status"):
            command_line.print_literal(json.dumps(self.status_report(), indent=2, ensure_ascii=False) + "\n")
            return 0
        scripted = False
        for item in opts.get("set") or []:
            scripted = True
            err = self.set_config_path(item)
            if err:
                command_line.printerr_literal(f"inkslate: {err}\n")
                return 1
        for item in opts.get("native") or []:
            scripted = True
            key, _sep, raw = item.partition("=")
            try:
                self.set_native(key.strip(), json.loads(raw))
            except ValueError as e:
                command_line.printerr_literal(f"inkslate: {item}: {e}\n")
                return 1
        if opts.get("action"):
            scripted = True
            action = opts["action"]
            if action == "open-page":
                self.open_page()
            elif action == "fullscreen":
                self.fullscreen()
            else:
                self.send_action(action)
        if not opts.get("background") and not scripted:
            self.activate()
        return 0

    def status_report(self):
        client = self.client()
        return {
            "version": __version__,
            "mode": self.config["mode"],
            "weylus": {"running": self.weylus.running, "message": self.weylus_message,
                       "path": self.weylus_path()},
            "usb": self.usb.status,
            "pages": [{"remote": c.remote, "version": c.hello.get("version"),
                       "fullscreen": c.state.get("fullscreen"), "visible": c.state.get("visible"),
                       "energysaving": (c.state.get("native") or {}).get("energysaving")}
                      for c in self.control.authed_clients],
            "active_page": bool(client),
        }

    def set_config_path(self, item):
        """Apply "a.b.c=<json>" to the config. Returns an error string or None."""
        path, sep, raw = item.partition("=")
        keys = [k for k in path.strip().split(".") if k]
        if not sep or not keys:
            return f"expected PATH=JSON, got {item!r}"
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw  # allow unquoted strings: mode=wifi
        node = self.config.data
        for k in keys[:-1]:
            if not isinstance(node.get(k), dict):
                return f"unknown setting {path}"
            node = node[k]
        if keys[-1] not in node:
            return f"unknown setting {path}"
        if keys[0] == "mode":
            self.set_mode(value)
            return None
        node[keys[-1]] = value
        self.config.save()
        if keys[0] in ("tablet", "profile"):
            self.tablet_config_changed()
        elif keys[0] == "usb":
            self.usb_config_changed()
        elif keys[0] in ("video", "access_code", "weylus_path"):
            self.weylus.restart()
        if self.window:
            self.window.refresh_config()
        return None

    def do_activate(self):
        if self.window is None:
            from .window import MainWindow
            self.window = MainWindow(self)
            self.window.connect("close-request", self._on_close_request)
        self.window.present()

    def _on_close_request(self, _win):
        if not self._hid_once:
            self._hid_once = True
            n = Gio.Notification.new(_("Inkslate is still running"))
            n.set_body(_("The tablet keeps working. Use “Quit” in the menu to stop it."))
            self.send_notification("background", n)
        return False  # hide_on_close hides it

    def do_shutdown(self):
        try:
            if self.usb.connected and self.usb.dimmed():
                self.usb._do_undim()     # synchronous: the worker is about to stop
            self.usb.stop()
            self.control.stop()
            self.weylus.stop()
        finally:
            Adw.Application.do_shutdown(self)

    # ------------------------------------------------------------------ helpers for the window

    def client(self):
        clients = self.control.authed_clients
        return clients[-1] if clients else None

    def weylus_path(self):
        return find_weylus(self.config["weylus_path"])

    def page_url(self):
        port = self.config["web_port"]
        if self.config["mode"] == "usb":
            return f"http://127.0.0.1:{port}/"
        return f"http://{lan_ip()}:{port}/?access_code={self.config['access_code']}"

    def set_native(self, key, value):
        self.control.send({"type": "set_native", "values": {key: value}})

    def send_action(self, action, **extra):
        self.control.send(dict({"type": "action", "action": action}, **extra))

    def tablet_config_changed(self):
        self.config.save()
        self.control.send({"type": "config", "config": self.config.tablet_config()})

    def usb_config_changed(self):
        self.config.save()
        self._apply_dimming()

    def open_page(self):
        self.usb.request("open_page")

    def fullscreen(self):
        self.usb.request("fullscreen")

    def restart_weylus(self):
        self.weylus.restart()

    def set_mode(self, mode):
        if mode == self.config["mode"]:
            return
        self.config["mode"] = mode
        self.config.save()
        self.control.stop()
        try:
            self.control.start()
        except GLib.Error as e:
            self._toast(e.message)
        self.weylus.restart()
        if self.window:
            self.window.refresh_config()
            self.window.refresh_status()

    def set_access_code(self, code):
        if not code or code == self.config["access_code"]:
            return
        self.config["access_code"] = code
        self.config.save()
        if self.config["mode"] == "wifi":
            self.weylus.restart()
        self._refresh_status()

    def set_monitor(self, on):
        if on != self._monitor:
            self._monitor = on
            self.control.send({"type": "monitor", "on": on})

    def open_log(self):
        Gio.AppInfo.launch_default_for_uri(Path(self.weylus.log_path).as_uri(), None)

    def autostart_enabled(self):
        return AUTOSTART_FILE.exists()

    def set_autostart(self, on):
        if on:
            AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
            AUTOSTART_FILE.write_text(
                "[Desktop Entry]\nType=Application\nName=Inkslate\n"
                f"Comment={_('Tablet pen input in the background')}\n"
                f"Exec={launcher_command()} --background\n"
                f"Icon={APP_ID}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")
        else:
            AUTOSTART_FILE.unlink(missing_ok=True)

    # ------------------------------------------------------------------ backend events

    def _refresh_status(self):
        if self.window:
            self.window.refresh_status()

    def _toast(self, text):
        if self.window:
            self.window.toast(text)

    def _on_weylus_state(self, running, message):
        self.weylus_message = message
        log.info("weylus %s %s", "running" if running else "stopped", message)
        if message == "crashed":
            n = Gio.Notification.new(_("Weylus keeps stopping"))
            n.set_body(_("Another Weylus may be using the same ports. See the log in Inkslate."))
            self.send_notification("weylus", n)
        self._refresh_status()

    def _on_hello(self, client):
        log.info("tablet page connected from %s (inkslate.js %s)", client.remote, client.hello.get("version"))
        self.control.send({"type": "config", "config": self.config.tablet_config()}, client)
        if self._monitor:
            self.control.send({"type": "monitor", "on": True}, client)
        client.fullscreen_requested = False
        self._refresh_status()

    def _on_tablet_state(self, client):
        s = client.state
        # A freshly loaded page over USB: put it in fullscreen once.
        if (self.config["mode"] == "usb" and self.config["usb"]["auto_fullscreen"]
                and self.usb.connected and not getattr(client, "fullscreen_requested", True)
                and s.get("visible") and not s.get("fullscreen")):
            client.fullscreen_requested = True
            log.info("page is not fullscreen, requesting it")
            self.usb.request("fullscreen")
        self._apply_dimming()
        if self.window:
            self.window.refresh_native()
            self.window.refresh_status()

    def _on_pressure(self, client, msg):
        if self.window and self._monitor:
            self.window.push_pressure(msg)

    def _on_tablet_closed(self, client):
        self._apply_dimming()
        if self.window:
            self.window.refresh_native()
            self.window.refresh_status()

    def _apply_dimming(self):
        if not self.usb.connected:
            return
        client = self.client()
        energy = bool(client and (client.state.get("native") or {}).get("energysaving"))
        want = self.config["usb"]["dim_on_energysaving"] and energy
        if want:
            self.usb.request("dim", level=self.config["usb"]["dim_level"])
        elif self.usb.dimmed():
            self.usb.request("undim")

    def _on_usb_status(self, status):
        self._refresh_status()

    def _on_usb_event(self, name, **info):
        if name == "connected" and info.get("newly"):
            if self.usb.dimmed() and not self.client():
                self.usb.request("undim")   # left dimmed by an unplug in energy saving
            if not (self.window and self.window.is_active()):
                n = Gio.Notification.new(_("Tablet connected"))
                n.set_body(_("Opening the page on the tablet."))
                self.send_notification("tablet", n)
        elif name == "fullscreen" and not info.get("ok"):
            self._toast(_("Could not switch to fullscreen: {reason}").format(reason=info.get("reason")))
        self._refresh_status()

    def _show_about(self):
        about = Adw.AboutDialog(
            application_name="Inkslate",
            application_icon=APP_ID,
            version=__version__,
            developer_name=_("Inkslate contributors"),
            license_type=Gtk.License.AGPL_3_0,
            website="https://github.com/melihcimen/inkslate",
            issue_url="https://github.com/melihcimen/inkslate/issues",
            comments=_("Use an Android tablet as a graphics tablet on Linux. Built on Weylus by H-M-H."),
        )
        about.add_credit_section(_("Built on"), ["Weylus https://github.com/H-M-H/Weylus"])
        about.present(self.window)


def lan_ip():
    """Best guess of the PC's LAN address (no packets are sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def launcher_command():
    import shutil
    installed = shutil.which("inkslate")
    if installed:
        return installed
    exe = Path(sys.argv[0]).resolve()
    if exe.name == "inkslate" and exe.exists():
        return str(exe)
    return f"{sys.executable} -m inkslate"


def main():
    return InkslateApp().run(sys.argv)
