"""Backend interface plus the in-memory demo backend.

A backend is the only thing that talks to a vCenter. Everything else (planner,
executor, GUI) works on plain data, which is why the same code runs unchanged
against a real vCenter 8.0, a real vCenter 9.0, or the built-in simulator.

Safety: backends are READ-ONLY unless the caller explicitly enters
``with backend.writes_enabled():``. Only the executor does that, and the only
write operations that exist are "create a folder" and "delete an empty folder
that we created".
"""

import contextlib
import time

from .model import new_inventory


class BackendError(Exception):
    """A problem talking to a vCenter, phrased so a human can act on it."""


class FolderExists(BackendError):
    pass


class Backend:
    is_demo = False

    def __init__(self):
        self._writes = False

    # -- write guard -------------------------------------------------------
    @contextlib.contextmanager
    def writes_enabled(self):
        self._writes = True
        try:
            yield self
        finally:
            self._writes = False

    def _require_writes(self):
        if not self._writes:
            raise BackendError("Internal safety check: write attempted while backend is read-only.")

    # -- interface ---------------------------------------------------------
    def info(self):
        raise NotImplementedError

    def capture(self):
        raise NotImplementedError

    def folder_children(self, dc, parent_path):
        """Names of the sub-folders directly under parent_path, read live."""
        raise NotImplementedError

    def folder_is_empty(self, dc, path):
        raise NotImplementedError

    def create_folder(self, dc, parent_path, name):
        raise NotImplementedError

    def delete_empty_folder(self, dc, path):
        raise NotImplementedError

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Demo backend
# ---------------------------------------------------------------------------

_SOURCE_FOLDERS = {
    "DC-East": [
        "Production", "Production/Web", "Production/App", "Production/Database",
        "Production/Database/Oracle", "Production/Database/SQL Server",
        "Development", "Development/Web", "Development/App",
        "Infrastructure", "Infrastructure/Active Directory", "Infrastructure/DNS-DHCP",
        "Infrastructure/Monitoring", "Templates", "Templates/Linux", "Templates/Windows",
        "Decommission-Pending",
    ],
    "DC-West": ["Production", "Production/Web", "Production/App", "DR-Replicas", "DR-Replicas/Tier1"],
    "DC-Branch": ["Branch Offices", "Branch Offices/Store-101", "Branch Offices/Store-102"],
}

_SOURCE_VMS = {
    "DC-East": [
        ("web-prod-01", "Production/Web", "poweredOn", "Ubuntu Linux (64-bit)"),
        ("web-prod-02", "Production/Web", "poweredOn", "Ubuntu Linux (64-bit)"),
        ("app-prod-01", "Production/App", "poweredOn", "Red Hat Enterprise Linux 9 (64-bit)"),
        ("ora-prod-01", "Production/Database/Oracle", "poweredOn", "Oracle Linux 8 (64-bit)"),
        ("sql-prod-01", "Production/Database/SQL Server", "poweredOn", "Microsoft Windows Server 2022"),
        ("web-dev-01", "Development/Web", "poweredOff", "Ubuntu Linux (64-bit)"),
        ("app-dev-01", "Development/App", "poweredOff", "Red Hat Enterprise Linux 9 (64-bit)"),
        ("dc01", "Infrastructure/Active Directory", "poweredOn", "Microsoft Windows Server 2022"),
        ("dc02", "Infrastructure/Active Directory", "poweredOn", "Microsoft Windows Server 2022"),
        ("dns01", "Infrastructure/DNS-DHCP", "poweredOn", "Debian GNU/Linux 12 (64-bit)"),
        ("mon01", "Infrastructure/Monitoring", "poweredOn", "Ubuntu Linux (64-bit)"),
        ("tmpl-ubuntu-2204", "Templates/Linux", "poweredOff", "Ubuntu Linux (64-bit)", True),
        ("tmpl-win2022", "Templates/Windows", "poweredOff", "Microsoft Windows Server 2022", True),
        ("old-app-legacy", "Decommission-Pending", "poweredOff", "CentOS 7 (64-bit)"),
        ("jumpbox", "", "poweredOn", "Ubuntu Linux (64-bit)"),
    ],
    "DC-West": [
        ("web-west-01", "Production/Web", "poweredOn", "Ubuntu Linux (64-bit)"),
        ("app-west-01", "Production/App", "poweredOn", "Red Hat Enterprise Linux 9 (64-bit)"),
        ("dr-sql-01", "DR-Replicas/Tier1", "poweredOff", "Microsoft Windows Server 2022"),
    ],
    "DC-Branch": [
        ("store101-fw", "Branch Offices/Store-101", "poweredOn", "Debian GNU/Linux 12 (64-bit)"),
        ("store102-fw", "Branch Offices/Store-102", "poweredOn", "Debian GNU/Linux 12 (64-bit)"),
    ],
}

# The 9.0 target is "partly built": some folders already exist, one differs only
# by letter case (a conflict), one datacenter is missing entirely, and it has an
# extra folder the source does not have. That shows every planner outcome.
_TARGET_FOLDERS = {
    "DC-East": ["Production", "Production/Web", "development", "Lab-Sandbox", "Templates"],
    "DC-West": [],
}


def _split(p):
    return tuple(p.split("/")) if p else ()


class DemoBackend(Backend):
    is_demo = True

    def __init__(self, role, delay=0.04):
        super().__init__()
        self.role = role
        self.delay = delay
        self.dcs = {}
        if role == "source":
            self._host, self._ver = "vcenter80.demo.local", "8.0.3"
            self._build = "24022515"
            for dc, folders in _SOURCE_FOLDERS.items():
                vms = []
                for i, row in enumerate(_SOURCE_VMS[dc]):
                    vms.append(dict(name=row[0], path=list(_split(row[1])), power=row[2], guest=row[3],
                                    uuid="5012%04d-demo-0000-0000-%012d" % (i, i), template=len(row) > 4))
                self.dcs[dc] = {"folders": {_split(f) for f in folders}, "vms": vms}
        else:
            self._host, self._ver = "vcenter90.demo.local", "9.0.0"
            self._build = "24755230"
            for dc, folders in _TARGET_FOLDERS.items():
                self.dcs[dc] = {"folders": {_split(f) for f in folders}, "vms": []}
        for d in self.dcs.values():  # keep the tree consistent: every parent exists
            for f in list(d["folders"]):
                for i in range(1, len(f)):
                    d["folders"].add(f[:i])

    def info(self):
        return {"host": self._host, "version": self._ver, "build": self._build,
                "full_name": "VMware vCenter Server %s build-%s (demo)" % (self._ver, self._build),
                "name": "VMware vCenter Server (demo)", "demo": True, "warnings": []}

    def capture(self):
        time.sleep(self.delay * 5)
        dcs = []
        for name in sorted(self.dcs):
            d = self.dcs[name]
            dcs.append({"name": name,
                        "folders": [list(f) for f in sorted(d["folders"], key=lambda f: (len(f), f))],
                        "vms": [dict(v) for v in d["vms"]]})
        return new_inventory(self.info(), dcs)

    def _dc(self, dc):
        if dc not in self.dcs:
            raise BackendError("Datacenter '%s' does not exist on %s." % (dc, self._host))
        return self.dcs[dc]

    def folder_children(self, dc, parent_path):
        d = self._dc(dc)
        parent = tuple(parent_path)
        if parent and parent not in d["folders"]:
            raise BackendError("Parent folder '%s' does not exist." % "/".join(parent))
        return sorted(f[-1] for f in d["folders"] if len(f) == len(parent) + 1 and f[:len(parent)] == parent)

    def folder_is_empty(self, dc, path):
        d = self._dc(dc)
        p = tuple(path)
        has_sub = any(len(f) > len(p) and f[:len(p)] == p for f in d["folders"])
        has_vm = any(tuple(v["path"]) == p for v in d["vms"])
        return not (has_sub or has_vm)

    def create_folder(self, dc, parent_path, name):
        self._require_writes()
        d = self._dc(dc)
        time.sleep(self.delay)
        if name in self.folder_children(dc, parent_path):
            raise FolderExists("A folder named '%s' already exists there." % name)
        d["folders"].add(tuple(parent_path) + (name,))

    def delete_empty_folder(self, dc, path):
        self._require_writes()
        d = self._dc(dc)
        time.sleep(self.delay)
        if tuple(path) not in d["folders"]:
            raise BackendError("Folder does not exist.")
        if not self.folder_is_empty(dc, path):
            raise BackendError("Folder is not empty; refusing to delete it.")
        d["folders"].discard(tuple(path))
