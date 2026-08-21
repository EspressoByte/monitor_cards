#!/usr/bin/env python3
"""Build an Access-Point-only device list from Catalyst 9800s over RESTCONF.

The second of two AP collection approaches (compare `../ssh_access_points/`).
Everything comes from the controller — no AP is contacted:

  GET .../access-point-oper-data/capwap-data      -> name, serial, model, IP, version
  GET .../access-point-oper-data/cdp-cache-data   -> upstream switch + port

Standard library only (`urllib` + `ssl`) — no paramiko, no RULES.md §1 exception.

Identity: the **chassis serial** is the AP's key, so renaming an AP on the
controller updates its existing card instead of creating a duplicate. APs are
only ever added or updated, never deleted (RULES.md §2).

Card back (6 rows): Site ID · Mgmt IP · Serial · Model · Location · Desc.
where Location is "<cdp-neighbor> · <cdp-port> · <wlc>" and Desc is the AP's
software version.

Run:
    python3 builder/api_access_points/build_aps.py --selftest   # no controller needed
    python3 builder/api_access_points/build_aps.py              # -> devices.aps.json
    python3 builder/api_access_points/build_aps.py --dump        # + save raw JSON
    python3 builder/api_access_points/build_aps.py --write       # -> ../../devices.json
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILDER = os.path.dirname(HERE)
ROOT = os.path.dirname(BUILDER)
sys.path.insert(0, BUILDER)  # reuse the builder's tested pure logic

import reconcile                          # noqa: E402
from jsonio import atomic_write_devices   # noqa: E402

import extract                                  # noqa: E402
import restconf                                 # noqa: E402

CONFIG_FILE = os.path.join(HERE, "config.json")
OUT_FILE = os.path.join(HERE, "devices.aps.json")
DEVICES_FILE = os.path.join(ROOT, "devices.json")
OVERRIDES_FILE = os.path.join(BUILDER, "overrides.json")
FIXTURES = os.path.join(HERE, "fixtures")
RAW_DIR = os.path.join(FIXTURES, "raw")

# Fields this builder fills, and therefore carries forward from the last good
# build when a controller stops answering.
AP_ENRICHED = ("model", "desc", "location", "serial", "wlc")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def check_config(config):
    """Return a list of problems ([] = usable)."""
    errs = []
    controllers = config.get("controllers") or []
    if not controllers:
        errs.append("config: `controllers` must list at least one 9800")
    for i, ctrl in enumerate(controllers):
        for key in ("host", "username", "password"):
            if not ctrl.get(key):
                errs.append(f"config: controllers[{i}].{key} is required")
    pattern = config.get("site_from_hostname")
    if pattern:
        try:
            re.compile(pattern)
        except re.error as e:
            errs.append(f"config: site_from_hostname is not a valid regex ({e})")
    return errs


# --------------------------------------------------------------------------- #
# collection
# --------------------------------------------------------------------------- #
def _dump(name, payload):
    os.makedirs(RAW_DIR, exist_ok=True)
    path = os.path.join(RAW_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"    dumped {path}")


def discover(config, dump=False):
    """Query every controller. Returns (records, warnings)."""
    site_pattern = config.get("site_from_hostname")
    timeout = config.get("timeout", restconf.DEFAULT_TIMEOUT)

    by_serial, unkeyed, warnings = {}, [], []
    for ctrl in config["controllers"]:
        host = ctrl["host"]
        ctx = restconf.build_ssl_context(ctrl.get("ca_bundle"),
                                         ctrl.get("verify_tls", True))
        args = dict(username=ctrl["username"], password=ctrl["password"],
                    port=ctrl.get("port", 443), timeout=timeout, context=ctx)

        name = restconf.get_hostname(host, **args) or host
        print(f"WLC {host} (as {name}):")

        capwap = restconf.get(host, restconf.CAPWAP_PATH, **args)
        try:
            cdp = restconf.get(host, restconf.CDP_PATH, **args)
        except restconf.RestconfError as e:
            # CDP is a bonus, not the point — don't lose the whole controller.
            warnings.append(f"{name}: no CDP data ({e})")
            cdp = {}
        if dump:
            _dump(f"{name}-capwap-data.json", capwap)
            _dump(f"{name}-cdp-cache-data.json", cdp)

        records, warns = extract.records_from_payloads(capwap, cdp, name, site_pattern)
        warnings.extend(warns)
        print(f"  {len(records)} AP(s) joined")

        for rec in records:
            serial = rec.get("serial")
            if not serial:
                unkeyed.append(rec)
                continue
            if serial in by_serial:
                warnings.append(
                    f"serial {serial} reported by both {by_serial[serial]['wlc']} "
                    f"and {name} — using {name}")
            by_serial[serial] = rec

    found = list(by_serial.values()) + unkeyed
    if found and not by_serial:
        warnings.append(
            "NO AP returned a serial number — the serial leaf path is almost "
            "certainly wrong for this IOS-XE version. Re-run with --dump and "
            "check fixtures/raw/*-capwap-data.json against extract.SERIAL_PATHS. "
            "Falling back to hostname keys for this run.")
    elif unkeyed:
        warnings.append(f"{len(unkeyed)} AP(s) had no serial; keyed by hostname")
    return found, warnings


# --------------------------------------------------------------------------- #
# merge — serial is the identity
# --------------------------------------------------------------------------- #
def merge_aps(prior, discovered, overrides, keep_non_aps):
    """Serial-keyed AP merge.

    Deliberately does NOT call reconcile.reconcile(): that treats the discovered
    list as authoritative for non-APs, so an AP-only list would delete every
    router, firewall and website from devices.json.

    Matching is serial first, then hostname. The hostname fallback is what lets
    this builder ADOPT records written by the SSH builder (which have no serial)
    — it stamps the serial onto the existing card instead of creating a second
    one. A serial match with a changed hostname is a rename: the record is
    updated in place, so there's no duplicate and no orphan.
    """
    prior_aps = [d for d in prior if reconcile.is_ap(d)]
    prior_other = [d for d in prior if not reconcile.is_ap(d)]

    by_serial, by_host = {}, {}
    for d in prior_aps:
        if d.get("serial"):
            by_serial.setdefault(str(d["serial"]), d)
        if d.get("hostname"):
            by_host.setdefault(d["hostname"], d)

    result, consumed, renames = [], set(), []
    for d in discovered:
        serial = str(d.get("serial") or "")
        match = by_serial.get(serial) if serial else None
        if match is not None and match.get("hostname") != d.get("hostname"):
            renames.append((match.get("hostname"), d.get("hostname"), serial))
        if match is None:
            match = by_host.get(d.get("hostname"))
        if match is not None:
            consumed.add(id(match))
        result.append(reconcile.apply_overrides(
            reconcile.merge_last_good(d, match, fields=AP_ENRICHED), overrides))

    # RULES.md §2: retain every prior AP this run didn't account for.
    for d in prior_aps:
        if id(d) not in consumed:
            result.append(reconcile.apply_overrides(d, overrides))

    if keep_non_aps:
        result.extend(prior_other)  # not ours to touch

    for d in result:
        # The AP's own location is only a fallback for APs with no CDP neighbor.
        fallback = d.pop("_ap_location", "")
        if not d.get("location"):
            d["location"] = fallback

    result.sort(key=lambda d: (str(d.get("site", "")), str(d.get("hostname", ""))))
    return result, renames


def duplicate_hostnames(devices):
    """Hostnames appearing more than once — server.py keys live status by hostname,
    so two cards sharing one would share a status dot."""
    seen, dupes = set(), set()
    for d in devices:
        host = d.get("hostname")
        if host in seen:
            dupes.add(host)
        seen.add(host)
    return sorted(dupes)


def build(config, write_devices=False, dump=False):
    target = DEVICES_FILE if write_devices else OUT_FILE
    overrides = load_json(OVERRIDES_FILE, {})

    # Last-good comes from the file we're about to write — and only that file, so
    # a staging run never drags unrelated APs out of the shared devices.json.
    prior = load_json(target, [])

    discovered, warnings = discover(config, dump=dump)
    devices, renames = merge_aps(prior, discovered, overrides,
                                 keep_non_aps=write_devices)

    for old, new, serial in renames:
        print(f"  renamed: {old} -> {new} (serial {serial})")

    valid = []
    for d in devices:
        errs = reconcile.validate(d)
        if errs:
            print(f"  ! skip {d.get('hostname', '?')}: {', '.join(errs)}", file=sys.stderr)
        else:
            valid.append(d)

    for host in duplicate_hostnames(valid):
        warnings.append(f"duplicate hostname {host!r} — two APs will share one status")
    for w in warnings:
        print(f"  ! {w}", file=sys.stderr)

    atomic_write_devices(valid, target)
    aps = sum(1 for d in valid if reconcile.is_ap(d))
    print(f"Wrote {len(valid)} device(s) — {aps} AP(s) — to {target}")


# --------------------------------------------------------------------------- #
# selftest
# --------------------------------------------------------------------------- #
def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def selftest():
    """Exercise the pure logic against fixture payloads — no controller needed."""
    site_pattern = r"^AP-(?P<site>[0-9]+)-"
    capwap, cdp = _fixture("capwap_data.json"), _fixture("cdp_cache_data.json")
    records, warns = extract.records_from_payloads(capwap, cdp, "wlc-01", site_pattern)
    assert not warns, warns
    assert len(records) == 4, records
    hosts = {r["hostname"]: r for r in records}

    # --- extraction, including every alternate leaf path (entry 2) ----------
    ap1 = hosts["AP-101-Floor2-01"]
    assert ap1["serial"] == "FGL2445ABCD", ap1
    assert ap1["model"] == "C9120AXI-B" and ap1["ip"] == "10.0.4.11", ap1
    assert ap1["desc"] == "17.9.4.27", ap1          # container-of-ints version
    assert ap1["site"] == "101", ap1
    ap2 = hosts["AP-101-Floor2-02"]                  # serial-number / ap-model /
    assert ap2["serial"] == "FGL2445WXYZ", ap2       # static-info.ip-addr / ap-name
    assert ap2["model"] == "C9120AXI-B" and ap2["ip"] == "10.0.4.12", ap2
    assert ap2["desc"] == "17.9.4.27", ap2           # plain-string version
    assert extract.format_version({"version": 17, "release": 9}) == "17.9"
    assert extract.format_version("17.3.5") == "17.3.5"
    assert extract.format_version(None) == ""

    # --- CDP join + the composed Location row -------------------------------
    assert ap1["location"] == "acc-sw-12 · Gi1/0/14 · wlc-01", ap1
    assert ap2["location"] == "acc-sw-12 · Te1/1/4 · wlc-01", ap2   # abbreviated
    # joined by ap-name only — that CDP entry carries no mac-addr
    assert hosts["lobby-ap-old-3"]["location"] == "acc-sw-44 · Fa0/12 · wlc-01"
    # no CDP entry at all -> the AP's own location, still stamped with the WLC
    assert hosts["AP-205-Lobby-01"]["location"] == "default location · wlc-01"
    assert extract.abbrev_interface("TenGigabitEthernet1/1/1") == "Te1/1/1"
    assert extract.norm_mac("AAAA.BBBB.0001") == extract.norm_mac("aa:aa:bb:bb:00:01")

    # --- site: hostname regex, falling back to the AP's location ------------
    assert hosts["lobby-ap-old-3"]["site"] == "Building C · Reception"

    # --- merge: serial is the identity --------------------------------------
    prior = [
        {"hostname": "core-rtr-01", "type": "Router", "ip": "10.0.0.1",
         "vendor": "Cisco", "model": "ISR 4451", "desc": "IOS-XE 17.9",
         "location": "DC-A · Rack 3", "site": "101", "status": "up"},
        # same AP as ap1 but under its OLD name -> must be renamed, not duplicated
        {"hostname": "AP-101-Floor2-01-OLD", "type": "Access Point", "ip": "10.0.4.11",
         "vendor": "Cisco", "model": "C9120AXI-B", "serial": "FGL2445ABCD",
         "wlc": "wlc-01", "desc": "17.9.4.27", "location": "acc-sw-12 · Gi1/0/14 · wlc-01",
         "site": "101", "status": "up"},
        # written by the SSH builder: no serial, matched by hostname and adopted
        {"hostname": "AP-205-Lobby-01", "type": "Access Point", "ip": "10.0.5.21",
         "vendor": "Cisco", "model": "C9130AXI-B", "desc": "17.9.4.27",
         "location": "old-sw · Gi1/0/2", "site": "205", "status": "up"},
        # not on any controller any more -> retained, never deleted
        {"hostname": "AP-101-Retired-99", "type": "Access Point", "ip": "10.0.4.99",
         "vendor": "Cisco", "model": "C9120AXI-B", "serial": "FGL0000GONE",
         "desc": "17.9.4.27", "location": "acc-sw-12 · Gi1/0/22", "site": "101",
         "status": "down"},
    ]
    merged, renames = merge_aps(prior, records, {}, keep_non_aps=True)
    out = {d["hostname"]: d for d in merged}

    assert ("AP-101-Floor2-01-OLD", "AP-101-Floor2-01", "FGL2445ABCD") in renames, renames
    assert "AP-101-Floor2-01-OLD" not in out, "renamed AP must not linger as an orphan"
    assert "AP-101-Floor2-01" in out, "renamed AP must survive under its new name"
    assert len([d for d in merged if d.get("serial") == "FGL2445ABCD"]) == 1, \
        "a rename must not produce a duplicate card"
    assert out["AP-205-Lobby-01"]["serial"] == "FGL2501LOBBY", \
        "an SSH-builder record must be adopted by hostname and gain its serial"
    assert out["AP-205-Lobby-01"]["location"] == "default location · wlc-01", \
        "adopted record takes the fresh location"
    assert "AP-101-Retired-99" in out, "prior AP must be retained (RULES.md §2)"
    assert "core-rtr-01" in out, "non-AP devices must survive an AP-only build"
    assert out["core-rtr-01"]["desc"] == "IOS-XE 17.9", "non-AP left untouched"
    assert not duplicate_hostnames(merged), duplicate_hostnames(merged)
    assert all(not reconcile.validate(d) for d in merged), "all records valid"

    # --- last-good carry-forward when a controller goes dark ----------------
    dark = [reconcile.mark_snmp_failed(
        {"hostname": "AP-101-Floor2-01", "type": "Access Point", "ip": "10.0.4.11",
         "vendor": "Cisco", "serial": "FGL2445ABCD", "status": "down"},
        source="RESTCONF")]
    again, _ = merge_aps(merged, dark, {}, keep_non_aps=True)
    kept = {d["hostname"]: d for d in again}["AP-101-Floor2-01"]
    assert kept["model"] == "C9120AXI-B", "last-good model kept"
    assert kept["location"] == "acc-sw-12 · Gi1/0/14 · wlc-01", "last-good CDP kept"
    assert kept["desc"].startswith("17.9.4.27") and "RESTCONF failed" in kept["desc"], kept
    third, _ = merge_aps(again, dark, {}, keep_non_aps=True)
    assert {d["hostname"]: d for d in third}["AP-101-Floor2-01"]["desc"].count("failed") == 1

    # --- AP-only output, and overrides --------------------------------------
    ap_only, _ = merge_aps(prior, records, {}, keep_non_aps=False)
    assert all(reconcile.is_ap(d) for d in ap_only), "devices.aps.json is AP-only"
    over, _ = merge_aps(prior, records, {"AP-205-Lobby-01": {"site": "205"}},
                        keep_non_aps=False)
    assert {d["hostname"]: d for d in over}["AP-205-Lobby-01"]["site"] == "205"

    # --- a wrong serial leaf path degrades to hostname keys, not to chaos ----
    blind = json.loads(json.dumps(capwap).replace("wtp-serial-num", "xxx-serial")
                       .replace("serial-number", "xxx-number"))
    stripped, _ = extract.records_from_payloads(blind, cdp, "wlc-01", site_pattern)
    assert stripped and all(not r["serial"] for r in stripped), "serials should be gone"
    fallback, _ = merge_aps(prior, stripped, {}, keep_non_aps=True)
    assert not duplicate_hostnames(fallback), \
        "with no serials, hostname matching must still avoid duplicate cards"

    print("extract            serial/model/IP/version + every alternate leaf path")
    print("cdp join           by MAC and by AP name; abbreviated; WLC appended")
    print("identity           rename-in-place, SSH-record adoption, no duplicates")
    print("retention          un-rediscovered APs kept; non-APs untouched")
    print("last-good          carried forward on a dark controller; notes don't stack")
    print("\nselftest: OK")


def main():
    ap = argparse.ArgumentParser(
        description="Build an AP-only device list from Catalyst 9800s over RESTCONF")
    ap.add_argument("--selftest", action="store_true",
                    help="run the pure-logic self-test against fixtures (no network)")
    ap.add_argument("--dump", action="store_true",
                    help="save each raw RESTCONF response to fixtures/raw/ as well")
    ap.add_argument("--write", action="store_true",
                    help=f"write {DEVICES_FILE} instead of {OUT_FILE}")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return

    config = load_json(CONFIG_FILE, None)
    if config is None:
        print(f"No config at {CONFIG_FILE} (copy config.example.json). Aborting.",
              file=sys.stderr)
        sys.exit(1)
    errs = check_config(config)
    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        sys.exit(1)

    try:
        build(config, write_devices=args.write, dump=args.dump)
    except restconf.RestconfError as e:
        print(f"RESTCONF failed: {e}", file=sys.stderr)
        print("Aborted without changing the device file.", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
