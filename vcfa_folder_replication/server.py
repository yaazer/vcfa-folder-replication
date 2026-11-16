"""Local GUI server. Standard library only.

Security model (it handles vCenter credentials, so it is deliberately strict):
  * binds to 127.0.0.1 only - never reachable from the network
  * every API call needs a random per-run token (given in the URL we open)
  * the Host header must be localhost (blocks DNS-rebinding)
  * passwords live in memory only, are never logged and never written to disk
"""

import json
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, executor
from .backends import BackendError, DemoBackend
from .model import InventoryError, summarize, to_csv, validate_inventory
from .planner import build_plan

WEB = Path(__file__).parent / "web"
STATIC = {"/app.js": "application/javascript", "/style.css": "text/css"}
CONFIRM_APPLY, CONFIRM_UNDO = "REPLICATE", "UNDO"


class ApiError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


class AppState:
    def __init__(self):
        self.lock = threading.RLock()
        self.demo = False
        self.reset()

    def reset(self):
        self.side = {"source": self._blank(), "target": self._blank()}
        self.plan = None
        self.job = None
        self.last_run = None

    @staticmethod
    def _blank():
        return {"backend": None, "inventory": None, "kind": None, "info": None}

    def close(self):
        for s in self.side.values():
            if s["backend"]:
                s["backend"].close()

    # -- views ---------------------------------------------------------------
    def view(self):
        def side(s):
            return {"connected": s["kind"] is not None, "kind": s["kind"], "info": s["info"],
                    "captured": summarize(s["inventory"]) if s["inventory"] else None,
                    "captured_at": s["inventory"]["captured_at"] if s["inventory"] else None}
        j = self.job
        return {"version": __version__, "demo": self.demo, "source": side(self.side["source"]),
                "target": side(self.side["target"]), "plan": self.plan,
                "job": None if not j else {k: j[k] for k in ("kind", "state", "done", "total", "log", "result")},
                "can_undo": bool(self.last_run and not self.last_run.get("dry_run") and not self.last_run.get("undone")
                                 and self.last_run.get("counts", {}).get("created"))}

    # -- actions ---------------------------------------------------------------
    def _set(self, role, backend, kind):
        s = self.side[role]
        if s["backend"]:
            s["backend"].close()
        s.update(backend=backend, kind=kind, info=backend.info() if backend else None, inventory=None)
        self.plan = None

    def start_demo(self):
        with self.lock:
            self.close()
            self.reset()
            self.demo = True
            self._set("source", DemoBackend("source"), "demo")
            self._set("target", DemoBackend("target"), "demo")

    def stop_demo(self):
        with self.lock:
            self.close()
            self.demo = False
            self.reset()

    def connect(self, role, b):
        from .vcenter import VCenterBackend
        if self.demo:
            raise ApiError("Leave demo mode before connecting to a real vCenter.")
        host, user, pw = (b.get("host") or "").strip(), (b.get("user") or "").strip(), b.get("password") or ""
        if not host or not user or not pw:
            raise ApiError("Host, username and password are all required.")
        port = int(b.get("port") or 443)
        backend = VCenterBackend(host, user, pw, port=port, insecure=bool(b.get("insecure")))
        with self.lock:
            other = self.side["target" if role == "source" else "source"]
            if other["info"] and other["info"].get("host") == backend.info()["host"] and other["kind"] == "live":
                backend.close()
                raise ApiError("Source and target are the same vCenter. Pick two different ones.")
            self._set(role, backend, "live")

    def disconnect(self, role):
        with self.lock:
            self._set(role, None, None)

    def capture(self, role):
        with self.lock:
            s = self.side[role]
            if not s["backend"]:
                raise ApiError("%s is not connected." % role.title())
            s["inventory"] = s["backend"].capture()
            self.plan = None
            return s["inventory"]

    def load_capture(self, inv):
        with self.lock:
            if self.demo:
                raise ApiError("Leave demo mode to load a capture file.")
            try:
                validate_inventory(inv)
            except InventoryError as e:
                raise ApiError(str(e))
            s = self.side["source"]
            if s["backend"]:
                s["backend"].close()
            s.update(backend=None, kind="file", info={**inv["vcenter"], "from_file": True}, inventory=inv)
            self.plan = None

    def make_plan(self):
        with self.lock:
            src, tgt = self.side["source"], self.side["target"]
            if not src["inventory"]:
                raise ApiError("Capture the source first.")
            if not tgt["backend"]:
                raise ApiError("The target must be connected.")
            tgt["inventory"] = tgt["backend"].capture()  # always fresh
            self.plan = build_plan(src["inventory"], tgt["inventory"])
            return self.plan

    def start_job(self, kind, dry_run=False):
        with self.lock:
            if self.job and self.job["state"] == "running":
                raise ApiError("Another operation is still running.", 409)
            tgt = self.side["target"]["backend"]
            if not tgt:
                raise ApiError("The target is not connected.")
            if kind == "apply":
                plan = self.make_plan()  # re-plan against the live target: never act on a stale plan
                total = plan["summary"]["create"]
            else:
                if not self.last_run:
                    raise ApiError("There is nothing to undo.")
                total = self.last_run["counts"]["created"]
            job = {"kind": kind, "state": "running", "done": 0, "total": total, "log": [], "result": None}
            self.job = job

        def progress(done, total, entry):
            job["done"], job["total"] = done, total
            job["log"].append({"result": entry["result"], "message": entry["message"]})

        def run():
            try:
                if kind == "apply":
                    res = executor.execute(plan, tgt, dry_run=dry_run, progress=progress)
                    self.last_run = res
                else:
                    res = executor.rollback(self.last_run, tgt, progress=progress)
                    self.last_run["undone"] = True
                with self.lock:  # refresh the plan first, so "done" always comes with the new truth
                    try:
                        self.make_plan()
                    except Exception:
                        pass
                job["result"] = res
                job["state"] = "done"
            except Exception as e:  # keep the UI informed instead of dying silently
                job["result"], job["state"] = {"error": str(e)}, "error"

        threading.Thread(target=run, daemon=True).start()


def make_server(state, token, port=0):
    class Handler(BaseHTTPRequestHandler):
        server_version = "vcfa"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json", extra=None):
            data = body if isinstance(body, bytes) else (
                body.encode() if isinstance(body, str) else json.dumps(body).encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if "json" in ctype or "text" in ctype else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _authed(self, query):
            host = self.headers.get("Host", "").rsplit(":", 1)[0].strip("[]")
            if host not in ("127.0.0.1", "localhost"):
                return False
            given = self.headers.get("X-VCFA-Token") or (query.get("t") or [""])[0]
            return secrets.compare_digest(given, token)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/":
                if not self._authed(q):
                    return self._send(403, "Open the exact link printed by the app (it contains a one-time key).", "text/plain")
                return self._send(200, (WEB / "index.html").read_bytes(), "text/html")
            if u.path in STATIC:
                return self._send(200, (WEB / u.path[1:]).read_bytes(), STATIC[u.path])
            if not u.path.startswith("/api/"):
                return self._send(404, "Not found", "text/plain")
            if not self._authed(q):
                return self._send(403, {"error": "Forbidden"})
            try:
                if u.path == "/api/state":
                    return self._send(200, state.view())
                if u.path == "/api/inventory":
                    role = (q.get("role") or ["source"])[0]
                    inv = state.side[role]["inventory"] if role in state.side else None
                    if not inv:
                        raise ApiError("Nothing captured yet.", 404)
                    return self._send(200, inv)
                if u.path == "/api/export":
                    role, fmt = (q.get("role") or ["source"])[0], (q.get("format") or ["json"])[0]
                    inv = state.side[role]["inventory"] if role in state.side else None
                    if not inv:
                        raise ApiError("Nothing captured yet.", 404)
                    host = inv["vcenter"].get("host", "vcenter")
                    if fmt == "csv":
                        return self._send(200, to_csv(inv), "text/csv", {
                            "Content-Disposition": 'attachment; filename="%s.inventory.csv"' % host})
                    return self._send(200, json.dumps(inv, indent=2), "application/json", {
                        "Content-Disposition": 'attachment; filename="%s.capture.json"' % host})
                raise ApiError("Unknown endpoint.", 404)
            except ApiError as e:
                self._send(e.status, {"error": str(e)})

        def do_POST(self):
            u = urlparse(self.path)
            if not self._authed(parse_qs(u.query)):
                return self._send(403, {"error": "Forbidden"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 64 * 1024 * 1024:
                    raise ApiError("Request too large.", 413)
                body = json.loads(self.rfile.read(n) or b"{}")
                role = body.get("role")
                if role is not None and role not in ("source", "target"):
                    raise ApiError("Bad role.")
                p = u.path
                if p == "/api/demo":
                    state.start_demo() if body.get("enable", True) else state.stop_demo()
                elif p == "/api/connect":
                    state.connect(role, body)
                elif p == "/api/disconnect":
                    state.disconnect(role)
                elif p == "/api/capture":
                    state.capture(role)
                elif p == "/api/load-capture":
                    state.load_capture(body.get("inventory"))
                elif p == "/api/plan":
                    state.make_plan()
                elif p == "/api/apply":
                    dry = bool(body.get("dry_run", True))
                    if not dry and body.get("confirm") != CONFIRM_APPLY:
                        raise ApiError("Type %s to confirm." % CONFIRM_APPLY)
                    state.start_job("apply", dry)
                elif p == "/api/undo":
                    if body.get("confirm") != CONFIRM_UNDO:
                        raise ApiError("Type %s to confirm." % CONFIRM_UNDO)
                    state.start_job("undo")
                else:
                    raise ApiError("Unknown endpoint.", 404)
                self._send(200, state.view())
            except ApiError as e:
                self._send(e.status, {"error": str(e)})
            except (BackendError, InventoryError) as e:
                self._send(400, {"error": str(e)})
            except json.JSONDecodeError:
                self._send(400, {"error": "Malformed request."})
            except Exception as e:  # never leak a traceback (or anything sensitive) to the page
                print("Unexpected error:", type(e).__name__, e, file=sys.stderr)
                self._send(500, {"error": "Unexpected error: %s" % type(e).__name__})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(port=0, demo=False, open_browser=True):
    state, token = AppState(), secrets.token_urlsafe(24)
    if demo:
        state.start_demo()
    httpd = make_server(state, token, port)
    url = "http://127.0.0.1:%d/?t=%s" % (httpd.server_address[1], token)
    print("vcfa-folder-replication %s" % __version__)
    print("Open this link in your browser (Ctrl+C here to quit):\n\n    %s\n" % url)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        state.close()
        httpd.server_close()
