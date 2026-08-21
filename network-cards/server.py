#!/usr/bin/env python3
"""Network Cards backend — serves the web UI and keeps device status current.

A background thread TCP-connects to each device's SSH port on an interval and
records reachability. The page polls /api/status to update the cards live.

Standard library only (http.server, socket, threading, json) — no installs.

Run:  python3 server.py   then open  http://localhost:8000
"""

import json
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import comms

HERE = os.path.dirname(os.path.abspath(__file__))
DEVICES_FILE = os.path.join(HERE, "devices.json")
STATE_FILE = os.path.join(HERE, "state.json")  # live status persisted across restarts
COMMS_FILE = os.path.join(HERE, "comms", "config.json")  # notification channels (gitignored)

HOST = "127.0.0.1"
PORT = 8000
POLL_INTERVAL = 5.0   # seconds between reachability sweeps
CONNECT_TIMEOUT = 1.0  # per-device TCP connect timeout
DEFAULT_PORT = 22      # SSH
MAX_WORKERS = 100     # cap concurrent probes (also bounds open sockets/FDs)

# Website monitoring: a device with a "url" is checked over HTTP instead of a
# raw TCP connect. 2xx/3xx -> up, 4xx/5xx -> warn (reachable but unhealthy),
# connect/DNS/timeout -> down, invalid TLS cert -> warn (reachable, cert bad).
# 8s (> POLL_INTERVAL): slow-but-reachable hosts vary a lot; a tighter timeout
# made them intermittently trip and flap red. Probes run concurrently, so a slow
# site only stretches its own sweep, not the others.
HTTP_TIMEOUT = 8.0
USER_AGENT = "NetworkCards/1.0 (+health-check)"


def _build_ssl_context():
    """SSL context for HTTPS probes, with a working CA store.

    The python.org macOS build ships without a configured CA store, so the
    default context can't verify ANY cert — that would mark every HTTPS site
    "warn" (degraded). When no certs are loaded, fall back to the already-
    installed `certifi` bundle so real, valid certs verify correctly and only
    genuinely bad/expired certs read as degraded.
    """
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs():
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except Exception:
            pass  # no certifi: behave as before (HTTPS may read degraded)
    return ctx


_SSL_CTX = _build_ssl_context()

# Devices loaded once at startup; live status lives in `_status` (hostname->state).
DEVICES = []
_status = {}
_since = {}    # hostname -> epoch when the current non-online streak began
_changed = {}  # hostname -> epoch of the most recent status change (any -> any)
_status_lock = threading.Lock()

ALERTER = None  # comms.Alerter when notifications are configured, else None


def load_devices():
    global DEVICES
    with open(DEVICES_FILE, encoding="utf-8") as f:
        DEVICES = json.load(f)
    for d in DEVICES:
        d["mgmtIp"] = d.get("ip")  # mgmt IP tracks the device IP (None for url-only sites)
    with _status_lock:
        for d in DEVICES:
            _status.setdefault(d["hostname"], d.get("status", "down"))


def load_state():
    """Restore persisted live status so a restart keeps downtime timers and
    last-changed times. Only entries for devices still present are applied."""
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            saved = json.load(f)
    except (FileNotFoundError, ValueError):
        return
    known = {d["hostname"] for d in DEVICES}
    with _status_lock:
        for h, info in saved.get("status", {}).items():
            if h in known:
                _status[h] = info
        for h, t in saved.get("since", {}).items():
            if h in known:
                _since[h] = t
        for h, t in saved.get("changed", {}).items():
            if h in known:
                _changed[h] = t


def save_state():
    """Atomically write the live status dicts to STATE_FILE."""
    with _status_lock:
        snapshot = {"status": dict(_status), "since": dict(_since),
                    "changed": dict(_changed)}
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f)
        os.replace(tmp, STATE_FILE)  # atomic; never leaves a half-written file
    except OSError:
        pass  # persistence is best-effort; never crash the poll loop over it


def probe(ip, port):
    """TCP-connect once. Returns 'up' / 'warn' / 'down'."""
    try:
        with socket.create_connection((ip, port), timeout=CONNECT_TIMEOUT):
            return "up"
    except ConnectionRefusedError:
        # Host answered but the port is closed — reachable, service down.
        return "warn"
    except (socket.timeout, OSError):
        return "down"


def http_probe(url):
    """Fetch a URL once and classify it as 'up' / 'warn' / 'down'."""
    def status_for(method):
        req = urllib.request.Request(url, method=method,
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=_SSL_CTX) as resp:
            return resp.status  # redirects already followed by the opener

    try:
        try:
            code = status_for("HEAD")
        except urllib.error.HTTPError as e:
            # Some servers reject HEAD; retry with GET before giving a verdict.
            if e.code in (405, 501):
                code = status_for("GET")
            else:
                code = e.code  # the server answered (4xx/5xx) -> reachable
        return "up" if 200 <= code < 400 else "warn"
    except urllib.error.HTTPError as e:
        return "up" if 200 <= e.code < 400 else "warn"
    except urllib.error.URLError as e:
        # Reachable but the TLS cert is invalid/expired -> degraded, not down.
        if isinstance(e.reason, ssl.SSLCertVerificationError):
            return "warn"
        return "down"
    except (socket.timeout, OSError):
        return "down"


def probe_device(d):
    """Probe one device; returns (hostname, state).

    A device with a "url" is a website (checked over HTTP); everything else is
    reached by a raw TCP connect to its port.
    """
    if d.get("url"):
        return d["hostname"], http_probe(d["url"])
    return d["hostname"], probe(d["ip"], d.get("checkPort", DEFAULT_PORT))


def poll_loop():
    # One reusable pool, capped so we never open more than MAX_WORKERS sockets
    # at once even with hundreds of devices. Probing concurrently keeps a sweep
    # near one CONNECT_TIMEOUT regardless of how many devices are unreachable.
    workers = min(MAX_WORKERS, max(1, len(DEVICES)))
    ip_by_host = {d["hostname"]: d.get("ip") for d in DEVICES}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            start = time.monotonic()
            # list() waits for every probe outside the lock; then update fast.
            results = list(pool.map(probe_device, DEVICES))
            with _status_lock:
                now = time.time()
                for hostname, state in results:
                    old = _status.get(hostname)
                    if old is not None and state != old:
                        _changed[hostname] = now            # status transition
                    if state == "up":
                        _since.pop(hostname, None)          # online: no timer
                    elif state != old or hostname not in _since:
                        _since[hostname] = now              # entered this status
                    # else: same non-online status -> keep its start time
                    _status[hostname] = state
            save_state()  # persist so a restart resumes timers/transitions
            # Notifications: debounced/deduped, outside the lock; delivery is
            # queued off-thread so a slow webhook never stalls the sweep.
            if ALERTER is not None:
                for hostname, state in results:
                    ALERTER.observe(hostname, state, now, ip_by_host.get(hostname))
            # Hold a steady cadence: sleep only the remainder of the interval.
            time.sleep(max(0.0, POLL_INTERVAL - (time.monotonic() - start)))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def end_headers(self):
        # No caching, so edits to html/css/js show on a normal refresh.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _send_json(self, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/status"):
            with _status_lock:
                payload = {h: {"status": s, "since": _since.get(h),
                               "changed": _changed.get(h)}
                           for h, s in _status.items()}
            self._send_json(payload)
            return
        if self.path.startswith("/api/devices"):
            with _status_lock:
                devices = [dict(d, status=_status.get(d["hostname"], "down"),
                                since=_since.get(d["hostname"]),
                                changed=_changed.get(d["hostname"]))
                           for d in DEVICES]
            self._send_json(devices)
            return
        super().do_GET()

    def log_message(self, *args):
        pass  # keep the console quiet; flip to super().log_message for debugging


def setup_comms():
    """Load notification channels and, if any are enabled, arm the alerter."""
    global ALERTER
    providers, settings = comms.load_comms(COMMS_FILE)
    if not providers:
        print("Comms: no channels enabled (see comms/config.example.json)")
        return
    names = ", ".join(p["name"] for p in providers)
    print(f"Comms: {names} enabled")
    comms.configure(settings)
    comms.start_worker()
    comms.send(providers, "Network Cards started",
               f"Monitoring {len(DEVICES)} devices. Alerts enabled for: {names}.")
    ALERTER = comms.Alerter(providers, debounce=settings["debounce"],
                            alert_warn=settings["alert_warn"])


def main():
    load_devices()
    load_state()
    setup_comms()
    threading.Thread(target=poll_loop, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Network Cards running at http://{HOST}:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
