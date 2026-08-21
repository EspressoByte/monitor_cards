"""Pure RESTCONF-JSON -> device-record mapping for Catalyst 9800 APs.

No network I/O and no third-party imports, so every function here is covered by
`build_aps.py --selftest`. Stdlib only.

The exact YANG leaf paths shift between IOS-XE releases, so **every field tries an
ordered list of candidate paths and takes the first that resolves**. When a field
comes back empty across a whole fleet that's a wrong-path signal, not a device
problem — `build_aps.py` warns and points at `--dump`.
"""

import re

# RESTCONF wraps list responses in a module-qualified key, e.g.
# {"Cisco-IOS-XE-wireless-access-point-oper:capwap-data": [ ... ]}.
_LIST_KEYS = {
    "capwap": ("Cisco-IOS-XE-wireless-access-point-oper:capwap-data", "capwap-data"),
    "cdp": ("Cisco-IOS-XE-wireless-access-point-oper:cdp-cache-data", "cdp-cache-data"),
}

# --- candidate leaf paths, most-likely first ------------------------------- #
SERIAL_PATHS = (
    "device-detail.static-info.board-data.wtp-serial-num",
    "device-detail.static-info.board-data.serial-number",
    "device-detail.static-info.board-data.ap-serial-num",
)
MODEL_PATHS = (
    "device-detail.static-info.ap-models.model",
    "device-detail.static-info.ap-models.ap-model",
    "device-detail.static-info.ap-model",
    "ap-model",
)
IP_PATHS = (
    "ip-addr",
    "device-detail.static-info.ip-addr",
    "ap-ip-addr",
)
NAME_PATHS = ("name", "ap-name", "wtp-name")
VERSION_PATHS = (
    "device-detail.wtp-version.sw-version",
    "device-detail.wtp-version.sw-ver",
    "device-detail.wtp-version.backup-sw-version",
    "sw-version",
)
LOCATION_PATHS = ("ap-location.location", "location", "ap-location")
SITE_TAG_PATHS = ("tag-info.site-tag.site-tag-name", "site-tag.site-tag-name")
MAC_PATHS = ("wtp-mac", "mac-addr", "device-detail.static-info.board-data.wtp-enet-mac")

CDP_NEIGHBOR_PATHS = ("cdp-cache-device-id", "cdp-cache-device-name", "device-id")
CDP_PORT_PATHS = (
    "cdp-cache-device-port",
    "cdp-cache-neighbour-port",
    "cdp-cache-neighbor-port",
    "cdp-cache-port-id",
)
CDP_MAC_PATHS = ("mac-addr", "wtp-mac", "ap-mac")
CDP_APNAME_PATHS = ("ap-name", "wtp-name")


# --------------------------------------------------------------------------- #
# generic helpers
# --------------------------------------------------------------------------- #
def dig(obj, path):
    """Walk a dotted path through nested dicts. Returns None if it doesn't resolve."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def first(obj, paths, default=""):
    """First path that resolves to something non-empty."""
    for path in paths:
        val = dig(obj, path)
        if val not in (None, "", {}, []):
            return val
    return default


def as_list(payload, kind):
    """Unwrap a RESTCONF list response into a plain list of entries."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in _LIST_KEYS.get(kind, ()):
            val = payload.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                return [val]
        # Unknown wrapper key: fall back to the first list of dicts we can find.
        for key, val in payload.items():
            if key.startswith("_"):
                continue
            if isinstance(val, list) and (not val or isinstance(val[0], dict)):
                return val
    return []


def format_version(val):
    """AP software version, from either shape the model uses.

    A plain string comes through as-is; the container form
    {version:17, release:9, maint:4, build:27} becomes "17.9.4.27".
    """
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, dict):
        parts = [val.get(k) for k in ("version", "release", "maint", "build")]
        nums = [str(p) for p in parts if isinstance(p, int)]
        return ".".join(nums)
    return ""


def norm_mac(mac):
    """Normalise any MAC notation to bare lowercase hex for reliable joining."""
    return re.sub(r"[^0-9a-f]", "", (mac or "").lower())


# --------------------------------------------------------------------------- #
# CDP
# --------------------------------------------------------------------------- #
# Longest prefixes first so TenGigabitEthernet never matches as GigabitEthernet.
_IFACE_ABBREV = (
    ("FortyGigabitEthernet", "Fo"),
    ("TwentyFiveGigE", "Twe"),
    ("TenGigabitEthernet", "Te"),
    ("GigabitEthernet", "Gi"),
    ("HundredGigE", "Hu"),
    ("FastEthernet", "Fa"),
    ("Port-channel", "Po"),
    ("Ethernet", "Et"),
)


def abbrev_interface(name):
    """'GigabitEthernet1/0/14' -> 'Gi1/0/14' (what engineers actually read)."""
    name = name or ""
    for full, short in _IFACE_ABBREV:
        if name.lower().startswith(full.lower()):
            return short + name[len(full):]
    return name


def index_cdp(cdp_entries):
    """Index CDP neighbours by normalised AP MAC and by AP name.

    Both keys are kept because the MAC leaf isn't always populated; the caller
    tries MAC first (exact) and falls back to the name.
    """
    by_mac, by_name = {}, {}
    for entry in cdp_entries:
        neighbor = str(first(entry, CDP_NEIGHBOR_PATHS)).split(".")[0]
        port = abbrev_interface(str(first(entry, CDP_PORT_PATHS)))
        if not neighbor:
            continue
        pair = (neighbor, port)
        mac = norm_mac(first(entry, CDP_MAC_PATHS))
        if mac:
            by_mac.setdefault(mac, pair)
        name = first(entry, CDP_APNAME_PATHS)
        if name:
            by_name.setdefault(name, pair)
    return by_mac, by_name


def compose_location(neighbor, port, wlc, fallback=""):
    """Build the card's Location row: '<neighbor> · <port> · <wlc>'.

    Falls back to the AP's own location when CDP gave us nothing, so the row is
    never empty. The controller is always appended when known.
    """
    if neighbor and port:
        head = f"{neighbor} · {port}"
    else:
        head = neighbor or fallback
    return f"{head} · {wlc}" if wlc and head else (head or wlc or "")


# --------------------------------------------------------------------------- #
# site
# --------------------------------------------------------------------------- #
def site_from_hostname(hostname, pattern, fallback=""):
    """Pull a site code out of an AP name via a configured regex.

    Uses the named group `site` when the pattern defines one, else group 1.
    Falls back to the AP's location when the name doesn't match. (Site tags are
    deliberately not used — same behaviour as the SSH builder.)
    """
    if pattern:
        try:
            m = re.search(pattern, hostname or "")
        except re.error:
            m = None  # caller validates the pattern at startup and warns
        if m:
            site = m.groupdict().get("site")
            if site is None and m.groups():
                site = m.group(1)
            if site:
                return site
    return fallback


# --------------------------------------------------------------------------- #
# record building
# --------------------------------------------------------------------------- #
def ap_record(entry, cdp_by_mac, cdp_by_name, wlc, site_pattern):
    """One capwap-data entry -> a devices.json record."""
    hostname = str(first(entry, NAME_PATHS))
    mac = norm_mac(first(entry, MAC_PATHS))
    ap_location = str(first(entry, LOCATION_PATHS))

    neighbor, port = cdp_by_mac.get(mac) or cdp_by_name.get(hostname) or ("", "")

    return {
        "hostname": hostname,
        "type": "Access Point",
        "ip": str(first(entry, IP_PATHS)),
        "vendor": "Cisco",  # not on the card back any more, but the FRONT uses it
        "model": str(first(entry, MODEL_PATHS)),
        "serial": str(first(entry, SERIAL_PATHS)),
        "wlc": wlc,
        "desc": format_version(first(entry, VERSION_PATHS, default=None)),
        "location": compose_location(neighbor, port, wlc, fallback=ap_location),
        "site": site_from_hostname(hostname, site_pattern, ap_location),
        "status": "down",  # seed only; server.py owns live status
        "_ap_location": ap_location,  # fallback if a later run loses CDP; stripped on write
    }


def records_from_payloads(capwap_payload, cdp_payload, wlc, site_pattern):
    """Both RESTCONF payloads for one controller -> (records, warnings)."""
    aps = as_list(capwap_payload, "capwap")
    cdp_by_mac, cdp_by_name = index_cdp(as_list(cdp_payload, "cdp"))

    records, warnings = [], []
    for entry in aps:
        rec = ap_record(entry, cdp_by_mac, cdp_by_name, wlc, site_pattern)
        if not rec["hostname"] or not rec["ip"]:
            warnings.append(
                f"{wlc}: skipping AP with no name/IP (mac={first(entry, MAC_PATHS)!r}) "
                f"— check the leaf paths with --dump")
            continue
        records.append(rec)
    return records, warnings
