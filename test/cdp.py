"""Minimal Chrome DevTools Protocol client.

No third-party deps: this machine has no pip, no websockets, no playwright, so the
RFC6455 client framing is hand-rolled here. Text frames only, which is all CDP uses.
"""

import base64
import json
import os
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request


class WS:
    def __init__(self, url, timeout=60.0):
        assert url.startswith("ws://"), url
        rest = url[5:]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=10)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key)
        )
        self.sock.sendall(req.encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise IOError("websocket handshake closed early")
            self.buf += chunk
        head, _, self.buf = self.buf.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise IOError("websocket handshake failed: %r" % head[:200])

    # --- raw io -------------------------------------------------------------
    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf)))
            if not chunk:
                raise IOError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        payload = text.encode()
        n = len(payload)
        if n < 126:
            hdr = struct.pack("!BB", 0x81, 0x80 | n)
        elif n < 65536:
            hdr = struct.pack("!BBH", 0x81, 0x80 | 126, n)
        else:
            hdr = struct.pack("!BBQ", 0x81, 0x80 | 127, n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(hdr + mask + masked)

    def recv(self):
        """Return the next complete text message (reassembling fragments)."""
        parts = []
        while True:
            b0, b1 = struct.unpack("!BB", self._read(2))
            fin, opcode = b0 & 0x80, b0 & 0x0F
            length = b1 & 0x7F
            if length == 126:
                (length,) = struct.unpack("!H", self._read(2))
            elif length == 127:
                (length,) = struct.unpack("!Q", self._read(8))
            data = self._read(length) if length else b""
            if opcode == 0x9:                     # ping -> pong
                mask = os.urandom(4)
                self.sock.sendall(
                    struct.pack("!BB", 0x8A, 0x80 | len(data)) + mask
                    + bytes(b ^ mask[i % 4] for i, b in enumerate(data))
                )
                continue
            if opcode == 0xA:                     # pong
                continue
            if opcode == 0x8:
                raise IOError("websocket close frame")
            parts.append(data)
            if fin:
                return b"".join(parts).decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class Chrome:
    """Headless Chrome + one attached page target.

    The debug port is ALWAYS ephemeral (--remote-debugging-port=0) and the real one
    is read back from DevToolsActivePort inside this harness's own private profile.
    Ten agents share this box; a fixed port (9222, 9333, ...) lets Chrome attach to
    somebody else's already-running instance, and /json/list then hands you their
    page -- a test that grades another agent's game as your own. There is no port
    argument on purpose.
    """

    def __init__(self, window="960,900", extra_args=()):
        self.profile = tempfile.mkdtemp(prefix="pokeyellow-chrome-")
        args = [
            "/usr/bin/google-chrome",
            "--headless=new",
            "--remote-debugging-port=0",
            "--user-data-dir=" + self.profile,
            "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            "--disable-sync", "--disable-background-networking", "--no-sandbox",
            # audio is muted at the browser level as well as in the page
            "--mute-audio", "--autoplay-policy=no-user-gesture-required",
            "--window-size=" + window,
            "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
            "--hide-scrollbars",
            "about:blank",
        ]
        args.extend(extra_args)
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ws = None
        self.msg_id = 0
        self.events = []

        # the port Chrome actually took, from OUR profile dir -- never a guess
        portfile = os.path.join(self.profile, "DevToolsActivePort")
        deadline = time.time() + 30
        self.port = None
        while time.time() < deadline and self.port is None:
            if os.path.exists(portfile):
                try:
                    first = open(portfile).read().split("\n")[0].strip()
                    if first:
                        self.port = int(first)
                except Exception:
                    pass
            if self.port is None:
                time.sleep(0.1)
        if self.port is None:
            raise RuntimeError("Chrome never wrote DevToolsActivePort in %s" % self.profile)

        while time.time() < deadline:
            try:
                targets = json.loads(
                    urllib.request.urlopen("http://127.0.0.1:%d/json/list" % self.port, timeout=2).read()
                )
                pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
                if pages:
                    self.ws = WS(pages[0]["webSocketDebuggerUrl"])
                    break
            except Exception:
                time.sleep(0.25)
        if not self.ws:
            raise RuntimeError("could not attach to headless Chrome on port %d" % self.port)

    # --- protocol -----------------------------------------------------------
    def call(self, method, timeout=60, **params):
        self.msg_id += 1
        mid = self.msg_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        end = time.time() + timeout
        while time.time() < end:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s: %s" % (method, msg["error"]))
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)
        raise TimeoutError(method)

    def pump(self, seconds):
        """Let the page run, draining events. Never a bare sleep: events would
        otherwise pile up in the socket buffer and stall the next call()."""
        end = time.time() + seconds
        while True:
            left = end - time.time()
            if left <= 0:
                return
            self.ws.sock.settimeout(min(left, 1.0))
            try:
                msg = json.loads(self.ws.recv())
            except (socket.timeout, TimeoutError):
                continue
            except IOError:
                return
            finally:
                self.ws.sock.settimeout(60.0)
            if "method" in msg:
                self.events.append(msg)

    def console(self):
        out = []
        for e in self.events:
            if e.get("method") == "Runtime.consoleAPICalled":
                txt = " ".join(
                    str(a.get("value", a.get("description", ""))) for a in e["params"].get("args", [])
                )
                out.append((e["params"].get("type", "log"), txt))
            elif e.get("method") == "Log.entryAdded":
                out.append((e["params"]["entry"].get("level", "log"), e["params"]["entry"].get("text", "")))
            elif e.get("method") == "Runtime.exceptionThrown":
                d = e["params"]["exceptionDetails"]
                out.append(("exception", d.get("text", "") + " " + str(d.get("exception", {}).get("description", ""))))
        return out

    def eval(self, expr, await_promise=False, timeout=60):
        r = self.call("Runtime.evaluate", timeout=timeout, expression=expr,
                      returnByValue=True, awaitPromise=await_promise)
        if r.get("exceptionDetails"):
            raise RuntimeError("eval failed: %s" % r["exceptionDetails"].get("text"))
        return r.get("result", {}).get("value")

    def screenshot(self, path):
        r = self.call("Page.captureScreenshot", format="png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        return path

    def kill(self):
        if self.ws:
            self.ws.close()
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        import shutil
        shutil.rmtree(self.profile, ignore_errors=True)
