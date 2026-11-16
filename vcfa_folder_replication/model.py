"""Inventory data model.

An *inventory* is a plain JSON-serialisable dict::

    {
      "schema": 1,
      "captured_at": "2026-01-01T12:00:00+00:00",
      "vcenter": {"host": ..., "version": "8.0.3", "full_name": ..., "build": ...},
      "datacenters": [
        {"name": "DC1",
         "folders": [["Prod"], ["Prod", "Web"]],      # every VM folder, as path segments
         "vms": [{"name": "web01", "path": ["Prod", "Web"], "power": "poweredOn", ...}]}
      ]
    }

Paths are lists of segments (never "/"-joined strings) so folder names that
contain a slash can never be confused with a hierarchy separator.
"""

import csv
import io
from datetime import datetime, timezone

SCHEMA = 1
MAX_NAME_LEN = 80  # vSphere limit for inventory object names


class InventoryError(ValueError):
    pass


def path_str(path):
    return "/".join(path)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_inventory(vcenter_info, datacenters):
    return {
        "schema": SCHEMA,
        "captured_at": now_iso(),
        "vcenter": vcenter_info,
        "datacenters": datacenters,
    }


def summarize(inv):
    dcs = inv["datacenters"]
    return {
        "datacenters": len(dcs),
        "folders": sum(len(d["folders"]) for d in dcs),
        "vms": sum(len(d["vms"]) for d in dcs),
    }


def validate_inventory(inv):
    """Strictly validate an inventory loaded from an untrusted file."""
    if not isinstance(inv, dict) or inv.get("schema") != SCHEMA:
        raise InventoryError("Not a capture file produced by this tool (bad or missing schema).")
    if not isinstance(inv.get("vcenter"), dict) or not isinstance(inv.get("datacenters"), list):
        raise InventoryError("Capture file is missing 'vcenter' or 'datacenters'.")

    def ok_path(p):
        return isinstance(p, list) and all(isinstance(s, str) and s for s in p)

    seen = set()
    for dc in inv["datacenters"]:
        if not isinstance(dc, dict) or not isinstance(dc.get("name"), str) or not dc["name"]:
            raise InventoryError("A datacenter entry has no name.")
        if dc["name"] in seen:
            raise InventoryError("Duplicate datacenter name: %s" % dc["name"])
        seen.add(dc["name"])
        if not all(ok_path(p) and p for p in dc.get("folders", [])):
            raise InventoryError("Datacenter %s has a malformed folder list." % dc["name"])
        if not isinstance(dc.get("vms", []), list):
            raise InventoryError("Datacenter %s has a malformed VM list." % dc["name"])
        for vm in dc.get("vms", []):
            if not isinstance(vm, dict) or not ok_path(vm.get("path")) and vm.get("path") != []:
                raise InventoryError("Datacenter %s has a malformed VM entry." % dc["name"])
        dc.setdefault("folders", [])
        dc.setdefault("vms", [])
    return inv


def to_csv(inv):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["datacenter", "type", "path", "name", "power_state", "guest_os", "uuid", "template"])
    for dc in inv["datacenters"]:
        for p in sorted(dc["folders"], key=lambda x: [s.lower() for s in x]):
            w.writerow([dc["name"], "folder", path_str(p), p[-1], "", "", "", ""])
        for vm in sorted(dc["vms"], key=lambda v: (path_str(v["path"]).lower(), v["name"].lower())):
            w.writerow([dc["name"], "vm", path_str(vm["path"]), vm["name"], vm.get("power", ""),
                        vm.get("guest", ""), vm.get("uuid", ""), "yes" if vm.get("template") else ""])
    return out.getvalue()
