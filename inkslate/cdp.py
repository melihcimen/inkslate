"""Tiny Chrome DevTools Protocol client (stdlib only).

Used over `adb forward` to reach the tablet's Chrome: a page can't put itself into fullscreen
without a user gesture, but Runtime.evaluate with userGesture=true counts as one.
"""

import base64
import json
import os
import socket
import struct
from urllib.parse import urlparse
from urllib.request import urlopen


class CDPError(Exception):
    pass


class Connection:
    def __init__(self, ws_url, timeout=3.0):
        u = urlparse(ws_url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(1)
            if not chunk:
                raise CDPError("connection closed during handshake")
            resp += chunk
        if b" 101 " not in resp.split(b"\r\n")[0]:
            raise CDPError(resp.decode(errors="replace").splitlines()[0])
        self._next_id = 0
        self.events = []

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _send(self, text):
        data = text.encode()
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        self.sock.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise CDPError("connection closed")
            buf += chunk
        return buf

    def _recv(self):
        msg = b""
        while True:
            b1, b2 = self._recv_exact(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            payload = self._recv_exact(n)
            opcode = b1 & 0x0F
            if opcode == 0x8:
                raise CDPError("connection closed by browser")
            if opcode in (0x9, 0xA):  # ping/pong
                continue
            msg += payload
            if b1 & 0x80:
                return msg.decode()

    def call(self, method, **params):
        self._next_id += 1
        my_id = self._next_id
        self._send(json.dumps({"id": my_id, "method": method, "params": params}))
        while True:
            r = json.loads(self._recv())
            if r.get("id") == my_id:
                if "error" in r:
                    raise CDPError(r["error"].get("message", str(r["error"])))
                return r.get("result", {})
            if "method" in r:
                self.events.append(r)

    def evaluate(self, expression, user_gesture=False, await_promise=True):
        r = self.call("Runtime.evaluate", expression=expression, returnByValue=True,
                      awaitPromise=await_promise, userGesture=user_gesture)
        if "exceptionDetails" in r:
            raise CDPError(r["exceptionDetails"].get("text", "exception"))
        return r.get("result", {}).get("value")


def list_pages(port, timeout=3.0):
    with urlopen(f"http://127.0.0.1:{port}/json", timeout=timeout) as r:
        return [p for p in json.load(r) if p.get("type") == "page" and "webSocketDebuggerUrl" in p]


def close_page(port, page_id, timeout=3.0):
    with urlopen(f"http://127.0.0.1:{port}/json/close/{page_id}", timeout=timeout) as r:
        r.read()
