"""Atomic JSON writing for the device file.

Split out of build_devices.py so a builder can write devices.json without
importing the whole ISE/SNMP orchestrator (which pulls in classify, ise_client,
snmp and wlc_restconf at module scope). The AP builders need the writer, not the
pipeline. Stdlib only.
"""

import json
import os
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEVICES_FILE = os.path.join(os.path.dirname(HERE), "devices.json")


def atomic_write_devices(devices, path=DEVICES_FILE):
    """Write devices.json via temp + os.replace so a serve never sees a partial
    file and a failed build can't corrupt the existing one."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(devices, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
