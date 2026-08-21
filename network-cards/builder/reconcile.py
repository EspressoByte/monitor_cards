"""Pure reconciliation + validation logic for the device-file build.

No network I/O lives here, so it's directly unit-testable (see
build_devices.py --selftest). Implements the rules from BUILD.md and RULES.md §2.
Stdlib only.
"""

import re
from datetime import datetime, timezone

# Canonical device types we emit into devices.json.
CANON_TYPES = {
    "Router", "Switch", "Firewall", "Wireless Controller",
    "Access Point", "Server", "F5", "Website",
}

# Fields populated by SNMP/RESTCONF enrichment. When a build run doesn't refill
# one (e.g. SNMP failed), we carry the prior build's value forward (last-good).
ENRICHED_FIELDS = ("vendor", "model", "desc")

# Any previous "<SOURCE> failed <timestamp>" note, so they don't stack across
# builds (and a stale SNMP note clears when the source becomes SSH, or vice versa).
# The leading "|" is optional: a device that has never had a description carries
# the note alone, with no separator to anchor on.
_FAILNOTE_RE = re.compile(r"(?:\s*\|)?\s*[A-Za-z]+ failed \d{4}-\d{2}-\d{2}[^|]*$")


def _utcstamp(when=None):
    return (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")


def is_ap(dev):
    return dev.get("type") == "Access Point"


def mark_snmp_failed(dev, when=None, source="SNMP"):
    """Flag a device whose enrichment failed this run. reconcile() will then keep
    its last-good fields and append a '<source> failed' note to `desc`.

    `source` names the collection method so the note reads truthfully — "SNMP"
    for the ISE/SNMP builder, "SSH" for the AP builder (ssh_access_points/).
    """
    dev = dict(dev)
    dev["_snmp_failed"] = True
    dev["_snmp_failed_at"] = _utcstamp(when)
    dev["_snmp_failed_src"] = source
    return dev


def merge_last_good(discovered, prior, fields=ENRICHED_FIELDS):
    """Return `discovered` backfilled from `prior` for any enriched field this run
    didn't populate, and (if flagged) annotated with a collection-failure note in
    `desc` without losing the last-good description. Strips internal flags.

    `fields` is overridable because different builders enrich different fields —
    the AP builder also carries `location` forward, since that's where its CDP
    neighbor data lives.
    """
    out = dict(discovered)
    if prior:
        for f in fields:
            if not out.get(f) and prior.get(f):
                out[f] = prior[f]
        # carry the seed status forward so a rebuild doesn't reset it
        if not out.get("status") and prior.get("status"):
            out["status"] = prior["status"]

    failed = out.pop("_snmp_failed", False)
    failed_at = out.pop("_snmp_failed_at", None)
    source = out.pop("_snmp_failed_src", "SNMP")
    if failed:
        # strip any prior failure note so they don't stack across builds
        base = _FAILNOTE_RE.sub("", out.get("desc") or "").rstrip(" |").strip()
        note = f"{source} failed {failed_at or _utcstamp()}"
        out["desc"] = f"{base} | {note}" if base else note
    return out


def apply_overrides(dev, overrides):
    """Apply manual annotations (overrides[hostname] = partial dict) on top."""
    patch = overrides.get(dev.get("hostname"))
    return dict(dev, **patch) if patch else dev


def reconcile(prior, discovered, websites, overrides):
    """Produce the final device list.

    - prior:      list[dict]  previous devices.json
    - discovered: list[dict]  ISE devices (mapped) + SNMP/RESTCONF enrichment
    - websites:   list[dict]  admin-maintained website records (type=Website)
    - overrides:  dict[hostname -> partial dict]  manual annotations

    Rules (BUILD.md / RULES.md §2):
      * non-AP devices fully reconcile to `discovered` (add / update / delete)
      * Access Points are NEVER deleted: retain prior APs, add/update discovered
      * keep last-good enriched fields when a run didn't refill them
      * websites pass through from websites.json (not in ISE, not probed)
    """
    prior_by_host = {d["hostname"]: d for d in prior if d.get("hostname")}
    result = {}

    # Non-AP discovered devices are authoritative (add/update; undiscovered ones
    # simply never get added, i.e. deleted).
    for d in discovered:
        if is_ap(d) or not d.get("hostname"):
            continue
        result[d["hostname"]] = apply_overrides(
            merge_last_good(d, prior_by_host.get(d["hostname"])), overrides)

    # Access Points: retain every prior AP (never delete) ...
    for host, d in prior_by_host.items():
        if is_ap(d):
            result[host] = apply_overrides(d, overrides)
    # ... then add/update with discovered APs.
    for d in discovered:
        if not is_ap(d) or not d.get("hostname"):
            continue
        result[d["hostname"]] = apply_overrides(
            merge_last_good(d, prior_by_host.get(d["hostname"])), overrides)

    # Websites from the admin file pass straight through (override-able).
    for w in websites:
        if not w.get("hostname"):
            continue
        result[w["hostname"]] = apply_overrides(
            merge_last_good(w, prior_by_host.get(w["hostname"])), overrides)

    # Stable order (site, hostname) for clean diffs.
    return sorted(result.values(),
                  key=lambda d: (str(d.get("site", "")), str(d.get("hostname", ""))))


def validate(dev):
    """Return a list of schema problems for one device record ([] = valid)."""
    errs = []
    if not dev.get("hostname"):
        errs.append("missing hostname")
    t = dev.get("type")
    if not t:
        errs.append("missing type")
    elif t not in CANON_TYPES:
        errs.append(f"unknown type {t!r}")
    if t == "Website":
        if not dev.get("url"):
            errs.append("website missing url")
    elif not dev.get("ip"):
        errs.append("missing ip")
    return errs
