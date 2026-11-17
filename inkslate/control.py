"""WebSocket control channel between the PC app and the tablet page (libsoup 3, GLib main loop).

Tablet -> PC: hello, state (Weylus' native settings, fullscreen, ...), pressure samples.
PC -> tablet: config (Inkslate settings), set_native, monitor, action, get_state.
"""

import json
import logging
from urllib.parse import urlparse

import gi

gi.require_version("Soup", "3.0")
from gi.repository import GLib, Soup  # noqa: E402

log = logging.getLogger("inkslate")


class Client:
    def __init__(self, conn, remote):
        self.conn = conn
        self.remote = remote
        self.authed = False
        self.hello = {}
        self.state = {}


class ControlServer:
    def __init__(self, config, handlers):
        """handlers: dict with optional callables hello(client), state(client),
        pressure(client, msg), closed(client)."""
        self.config = config
        self.handlers = handlers
        self.server = None
        self.clients = []

    def start(self):
        self.stop()
        self.server = Soup.Server()
        self.server.add_websocket_handler("/control", None, None, self._on_websocket)
        port = self.config["control_port"]
        opts = Soup.ServerListenOptions.IPV4_ONLY
        if self.config["mode"] == "usb":
            self.server.listen_local(port, opts)
        else:
            self.server.listen_all(port, opts)

    def stop(self):
        for c in list(self.clients):
            c.conn.close(Soup.WebsocketCloseCode.GOING_AWAY, None)
        self.clients = []
        if self.server is not None:
            self.server.disconnect()
            self.server = None

    @property
    def authed_clients(self):
        return [c for c in self.clients if c.authed]

    def _origin_ok(self, origin):
        # Only the Weylus page may connect, not arbitrary sites open in a browser.
        if not origin:
            return False
        u = urlparse(origin)
        if u.port != self.config["web_port"]:
            return False
        if self.config["mode"] == "usb":
            return u.hostname in ("127.0.0.1", "localhost")
        return True

    def _on_websocket(self, server, msg, path, conn):
        origin = msg.get_request_headers().get_one("Origin")
        remote = msg.get_remote_host() or "?"
        if not self._origin_ok(origin):
            conn.close(Soup.WebsocketCloseCode.POLICY_VIOLATION, "origin")
            return
        client = Client(conn, remote)
        self.clients.append(client)
        conn.connect("message", self._on_message, client)
        conn.connect("closed", self._on_closed, client)

    def _on_message(self, conn, data_type, data, client):
        if data_type != Soup.WebsocketDataType.TEXT:
            return
        try:
            msg = json.loads(data.get_data().decode())
        except (ValueError, UnicodeDecodeError):
            return
        kind = msg.get("type")
        if not client.authed:
            if kind != "hello":
                return
            if self.config["mode"] == "wifi" and msg.get("code") != self.config["access_code"]:
                conn.close(Soup.WebsocketCloseCode.POLICY_VIOLATION, "access code")
                return
            client.authed = True
            client.hello = msg
            self._call("hello", client)
            return
        if kind == "state":
            client.state = msg
            self._call("state", client)
        elif kind == "pressure":
            self._call("pressure", client, msg)
        elif kind == "weylus_error":
            self._call("weylus_error", client, msg)

    def _on_closed(self, conn, client):
        if client in self.clients:
            self.clients.remove(client)
        if client.authed:
            self._call("closed", client)

    def _call(self, name, *args):
        fn = self.handlers.get(name)
        if fn:
            try:
                fn(*args)
            except Exception:  # keep the server alive on handler bugs
                log.exception("control handler %s failed", name)

    def send(self, msg, client=None):
        text = json.dumps(msg)
        for c in [client] if client else self.authed_clients:
            if c.conn.get_state() == Soup.WebsocketState.OPEN:
                c.conn.send_text(text)


def idle(fn, *args, **kwargs):
    """Run fn(*args, **kwargs) once in the GLib main loop (for worker threads)."""
    def _run():
        try:
            fn(*args, **kwargs)
        except Exception:
            log.exception("main loop callback %s failed", getattr(fn, "__name__", fn))
        return False
    GLib.idle_add(_run)
