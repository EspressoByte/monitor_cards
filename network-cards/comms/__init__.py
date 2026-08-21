#!/usr/bin/env python3
"""Outbound notifications for Network Cards (Teams / Slack / Discord).

Read `comms/config.json` at server start; each provider has an `enabled` toggle
so an engineer fills in only what they use. When a device confirms a state change
(debounced so flaps don't spam), send a message to every enabled channel.

Standard library only — all three providers are incoming webhooks, i.e. one
HTTPS POST of JSON via urllib. See comms/README.md for the design.
"""

import json
import queue
import ssl
import threading
import urllib.request

# Providers we ship adapters for. Order = the order they're listed/sent.
PROVIDERS = ("teams", "slack", "discord")

USER_AGENT = "NetworkCards/1.0 (+notify)"


def _build_ssl_context():
    """SSL context for the webhook POSTs, with a working CA store.

    The python.org macOS build ships without a configured CA store, so the
    default context can't verify ANY cert — every real webhook (Discord, Slack,
    Teams) would fail CERTIFICATE_VERIFY_FAILED. Fall back to the already-
    installed `certifi` bundle, mirroring server.py's HTTPS probes.
    """
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs():
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except Exception:
            pass  # no certifi: behave as before (verification may fail)
    return ctx


_SSL_CTX = _build_ssl_context()

# Tunables live in comms/config.json's optional "settings" block; these are the
# fallbacks when it's absent or a value is invalid. configure() overwrites the
# module-level runtime values below.
DEFAULTS = {
    "debounce": 2,           # consecutive sweeps a change must persist to alert
    "alert_warn": True,      # alert on up->warn (degraded), not just up->down
    "webhook_timeout": 10.0,  # seconds per POST
    "queue_max": 1000,       # max queued notifications before overflow is dropped
}

WEBHOOK_TIMEOUT = DEFAULTS["webhook_timeout"]  # per-POST; runtime value

# Delivery runs on a single background worker draining this queue, so a slow or
# failing webhook never blocks the poll loop, and a mass outage can't spawn an
# unbounded number of threads. maxsize bounds memory; overflow is dropped+logged.
_queue = queue.Queue(maxsize=DEFAULTS["queue_max"])
_worker_started = False
_worker_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_comms(path):
    """Return (providers, settings).

    providers: list of enabled channels [{'name', 'webhook_url'}, ...].
    settings:  DEFAULTS merged with comms/config.json's optional "settings" block.

    Best-effort: a missing or invalid file disables notifications rather than
    crashing the server. An enabled provider with a blank URL is skipped.
    """
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return [], dict(DEFAULTS)                    # no config.json -> off
    except (ValueError, OSError) as e:
        print(f"[comms] {path} unreadable ({e}); notifications disabled")
        return [], dict(DEFAULTS)

    if not isinstance(cfg, dict):
        cfg = {}
    providers = []
    blocks = cfg.get("providers", {})
    for name in PROVIDERS:
        p = blocks.get(name)
        if not isinstance(p, dict) or not p.get("enabled"):
            continue
        url = (p.get("webhook_url") or "").strip()
        if not url:
            print(f"[comms] '{name}' enabled but webhook_url is empty; skipping")
            continue
        providers.append({"name": name, "webhook_url": url})
    return providers, _load_settings(cfg.get("settings", {}))


def _load_settings(raw):
    """Validate the "settings" block, falling back to DEFAULTS per-key."""
    s = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return s

    def _num(key, cast, minimum):
        val = raw.get(key)
        if val is None:
            return
        try:
            val = cast(val)
        except (TypeError, ValueError):
            print(f"[comms] settings.{key}={val!r} invalid; using {s[key]}")
            return
        if val < minimum:
            print(f"[comms] settings.{key}={val} too small; using {s[key]}")
            return
        s[key] = val

    _num("debounce", int, 1)
    _num("webhook_timeout", float, 0.1)
    _num("queue_max", int, 1)
    if "alert_warn" in raw:
        if isinstance(raw["alert_warn"], bool):
            s["alert_warn"] = raw["alert_warn"]
        else:
            print(f"[comms] settings.alert_warn must be true/false; "
                  f"using {s['alert_warn']}")
    return s


def configure(settings):
    """Apply the module-level tunables (queue size + POST timeout).

    Call before start_worker() / send(). debounce and alert_warn are consumed by
    the Alerter, not here.
    """
    global _queue, WEBHOOK_TIMEOUT
    WEBHOOK_TIMEOUT = settings["webhook_timeout"]
    if _queue.maxsize != settings["queue_max"]:
        _queue = queue.Queue(maxsize=settings["queue_max"])


# --------------------------------------------------------------------------- #
# Per-provider payload formatting (each webhook wants a different JSON shape)
# --------------------------------------------------------------------------- #
def _payload(name, title, text):
    if name == "slack":
        return {"text": f"*{title}*\n{text}"}
    if name == "discord":
        return {"content": f"**{title}**\n{text}"}
    if name == "teams":
        # Power Automate "Workflows" webhook: an Adaptive Card in `attachments`.
        # (The retired O365 connector used a bare MessageCard — do not use it.)
        return {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": title,
                         "weight": "Bolder", "size": "Medium", "wrap": True},
                        {"type": "TextBlock", "text": text, "wrap": True},
                    ],
                },
            }],
        }
    return {"text": f"{title}\n{text}"}             # unknown -> plain-ish fallback


# --------------------------------------------------------------------------- #
# Delivery (background worker)
# --------------------------------------------------------------------------- #
def start_worker():
    """Start the single delivery worker once (idempotent)."""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(target=_drain, name="comms-delivery",
                         daemon=True).start()
        _worker_started = True


def _drain():
    while True:
        url, body, name = _queue.get()
        try:
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json",
                         "User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT,
                                        context=_SSL_CTX) as resp:
                resp.read()                         # drain so the socket frees
        except Exception as e:                      # noqa: BLE001 - best-effort
            print(f"[comms] {name} delivery failed: {e}")
        finally:
            _queue.task_done()


def send(providers, title, text):
    """Enqueue `title`/`text` for delivery to every provider (non-blocking)."""
    for p in providers:
        body = json.dumps(_payload(p["name"], title, text)).encode("utf-8")
        try:
            _queue.put_nowait((p["webhook_url"], body, p["name"]))
        except queue.Full:
            print(f"[comms] queue full; dropped {p['name']} notification")


# --------------------------------------------------------------------------- #
# Alerting: debounce state changes, then send one message per transition
# --------------------------------------------------------------------------- #
def _human(seconds):
    """Compact duration: 45s / 12m / 3h / 4d."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _decide(old, new):
    """Which alert (if any) a confirmed old->new transition warrants.

    Alert when a device leaves 'up' (down/degraded) or returns to 'up'
    (recovered). down<->warn reshuffles are still-bad noise -> no alert.
    """
    if new == "up":
        return "recovered"
    if old == "up" and new == "down":
        return "down"
    if old == "up" and new == "warn":
        return "degraded"
    return None


class Alerter:
    """Turns raw per-poll states into debounced, deduped notifications.

    Call observe() once per device per sweep. A change must persist for
    `debounce` consecutive sweeps before it's confirmed and (maybe) alerted,
    which absorbs flapping. The confirmed state is tracked separately from the
    live UI status, so cards still update instantly.
    """

    def __init__(self, providers, debounce=2, alert_warn=True):
        self.providers = providers
        self.debounce = max(1, int(debounce))
        self.alert_warn = alert_warn
        self._confirmed = {}        # host -> last confirmed state (baseline)
        self._since = {}            # host -> when confirmed state began
        self._pending = {}          # host -> [candidate_state, consecutive_count]

    def observe(self, host, state, now, ip=None):
        confirmed = self._confirmed.get(host)
        if confirmed is None:                       # first sight -> baseline only
            self._confirmed[host] = state
            self._since[host] = now
            return
        if state == confirmed:
            self._pending.pop(host, None)           # back to steady -> clear
            return

        cand = self._pending.get(host)
        if cand and cand[0] == state:
            cand[1] += 1
        else:
            cand = self._pending[host] = [state, 1]
        if cand[1] < self.debounce:
            return                                  # not stable enough yet

        # Transition confirmed.
        self._pending.pop(host, None)
        dur = _human(now - self._since.get(host, now))
        self._confirmed[host] = state
        self._since[host] = now

        kind = _decide(confirmed, state)
        if kind is None or (kind == "degraded" and not self.alert_warn):
            return
        title, text = _message(kind, host, ip, confirmed, dur)
        send(self.providers, title, text)


def _message(kind, host, ip, old, dur):
    who = f"{host} ({ip})" if ip else host
    if kind == "down":
        return (f"🔴 {who} is DOWN",
                f"{who} stopped responding — was up for {dur}.")
    if kind == "degraded":
        return (f"🟡 {who} is DEGRADED",
                f"{who} is reachable but unhealthy (warn) — was up for {dur}.")
    # recovered
    return (f"🟢 {who} RECOVERED",
            f"{who} is back UP — was {old} for {dur}.")
