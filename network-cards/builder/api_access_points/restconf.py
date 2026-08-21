"""Minimal RESTCONF GET client for the Catalyst 9800.

Standard library only (`urllib` + `ssl`) — no new dependency, unlike the SSH
builder's paramiko. See RULES.md §1.

RESTCONF is just HTTPS + JSON: a GET with `Accept: application/yang-data+json`
and HTTP Basic auth returns the YANG data as JSON. Only reads are performed.
"""

import base64
import json
import socket
import ssl
import urllib.error
import urllib.request

# YANG paths this builder reads. Both are sub-containers of access-point-oper-data
# rather than the whole container: fetching all of it drags in per-radio
# statistics and is very large on a populated controller.
CAPWAP_PATH = ("Cisco-IOS-XE-wireless-access-point-oper:"
               "access-point-oper-data/capwap-data")
CDP_PATH = ("Cisco-IOS-XE-wireless-access-point-oper:"
            "access-point-oper-data/cdp-cache-data")
HOSTNAME_PATH = "Cisco-IOS-XE-native:native/hostname"

ACCEPT = "application/yang-data+json"
USER_AGENT = "NetworkCards/1.0 (+ap-builder)"
DEFAULT_TIMEOUT = 30.0


class RestconfError(Exception):
    """Any failure talking to a controller's RESTCONF API."""


def build_ssl_context(ca_bundle=None, verify_tls=True):
    """TLS context for the controller, with a working CA store.

    Mirrors server.py's `_build_ssl_context()`: the python.org macOS build ships
    without a configured CA store, so the default context can't verify anything —
    fall back to the installed certifi bundle when no certs are loaded.
    """
    if not verify_tls:
        # Lab controllers commonly present a self-signed cert.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    ctx = ssl.create_default_context()
    if ca_bundle:
        try:
            ctx.load_verify_locations(ca_bundle)
        except OSError as e:
            raise RestconfError(f"cannot load ca_bundle {ca_bundle!r}: {e}") from e
        return ctx
    if not ctx.get_ca_certs():
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except Exception:
            pass  # no certifi: behave as before
    return ctx


def get(host, path, username, password, port=443, timeout=DEFAULT_TIMEOUT,
        context=None):
    """GET one RESTCONF path and return the decoded JSON."""
    url = f"https://{host}:{int(port or 443)}/restconf/data/{path}"
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    req = urllib.request.Request(url, headers={
        "Accept": ACCEPT,
        "Authorization": f"Basic {token}",
        "User-Agent": USER_AGENT,
    })

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        # Map the failures that actually happen to something actionable.
        if e.code in (401, 403):
            raise RestconfError(
                f"{host}: HTTP {e.code} — check the username/password and that the "
                f"account has RESTCONF/NETCONF read privilege") from e
        if e.code == 404:
            raise RestconfError(
                f"{host}: HTTP 404 for {path!r} — either RESTCONF isn't enabled "
                f"(`restconf` + `ip http secure-server`) or this IOS-XE version "
                f"uses different YANG paths (try --dump)") from e
        raise RestconfError(f"{host}: HTTP {e.code} for {path!r}: {e.reason}") from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLCertVerificationError):
            raise RestconfError(
                f"{host}: TLS verification failed ({e.reason}) — point `ca_bundle` "
                f"at the controller's CA, or set \"verify_tls\": false for a lab "
                f"controller") from e
        raise RestconfError(f"{host}: cannot reach RESTCONF: {e.reason}") from e
    except (socket.timeout, TimeoutError) as e:
        raise RestconfError(f"{host}: timed out after {timeout}s on {path!r}") from e
    except OSError as e:
        raise RestconfError(f"{host}: {type(e).__name__}: {e}") from e

    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as e:
        raise RestconfError(f"{host}: {path!r} did not return JSON: {e}") from e


def get_hostname(host, username, password, port=443, timeout=DEFAULT_TIMEOUT,
                 context=None):
    """The controller's configured hostname, for the card's `wlc` field.

    Best-effort: reading `native` config needs more privilege than the oper data,
    so fall back to the configured host string rather than failing the run.
    """
    try:
        data = get(host, HOSTNAME_PATH, username, password, port=port,
                   timeout=timeout, context=context)
    except RestconfError:
        return ""
    for key in ("Cisco-IOS-XE-native:hostname", "hostname"):
        val = data.get(key) if isinstance(data, dict) else None
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""
