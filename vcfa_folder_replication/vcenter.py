"""Real vCenter backend (pyvmomi). Works with vCenter Server 8.0 and 9.0.

Only the stable vSphere Web Services API is used (no version-specific calls),
and inventory is read with the PropertyCollector in bulk, so capturing
thousands of VMs takes seconds rather than minutes.
"""

import ssl
import time

from .backends import Backend, BackendError, FolderExists
from .model import new_inventory

try:  # imported lazily-safe so the demo / tests work without pyvmomi installed
    from pyVim.connect import Disconnect, SmartConnect
    from pyVmomi import vim, vmodl
except ImportError:  # pragma: no cover
    SmartConnect = None


class VCenterBackend(Backend):
    def __init__(self, host, user, password, port=443, insecure=False):
        super().__init__()
        if SmartConnect is None:
            raise BackendError("The 'pyvmomi' package is not installed. Run: pip install pyvmomi")
        self.host = host
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        try:
            self.si = SmartConnect(host=host, user=user, pwd=password, port=int(port), sslContext=ctx)
        except vim.fault.InvalidLogin:
            raise BackendError("Login failed for '%s' on %s. Check the username and password." % (user, host))
        except ssl.SSLError as e:
            raise BackendError("TLS certificate for %s was not trusted (%s). If it is a self-signed "
                               "certificate you trust, tick 'Accept self-signed certificate'." % (host, e))
        except (OSError, vim.fault.HostConnectFault) as e:
            raise BackendError("Could not reach %s:%s (%s)." % (host, port, e))
        except Exception as e:
            raise BackendError("Connection to %s failed: %s" % (host, e))
        self.content = self.si.RetrieveContent()
        about = self.content.about
        if about.apiType != "VirtualCenter":
            self.close()
            raise BackendError("%s is an ESXi host, not a vCenter Server. Connect to the vCenter instead." % host)
        self._dc_roots = {}

    # -- info ---------------------------------------------------------------
    def info(self):
        a = self.content.about
        warnings = []
        if a.version.split(".")[0] not in ("8", "9"):
            warnings.append("This tool is tested with vCenter 8.0 and 9.0; this one is %s." % a.version)
        return {"host": self.host, "version": a.version, "build": a.build, "full_name": a.fullName,
                "name": a.name, "demo": False, "warnings": warnings}

    # -- helpers ------------------------------------------------------------
    def _retrieve(self, obj_type, props):
        view = self.content.viewManager.CreateContainerView(self.content.rootFolder, [obj_type], True)
        pc = self.content.propertyCollector
        try:
            ts = vmodl.query.PropertyCollector.TraversalSpec(
                name="tv", type=vim.view.ContainerView, path="view", skip=False)
            ospec = vmodl.query.PropertyCollector.ObjectSpec(obj=view, skip=True, selectSet=[ts])
            pspec = vmodl.query.PropertyCollector.PropertySpec(type=obj_type, pathSet=props)
            fspec = vmodl.query.PropertyCollector.FilterSpec(objectSet=[ospec], propSet=[pspec])
            opts = vmodl.query.PropertyCollector.RetrieveOptions(maxObjects=1000)
            res = pc.RetrievePropertiesEx([fspec], opts)
            out = []
            while res:
                for o in res.objects:
                    d = {p.name: p.val for p in (o.propSet or [])}
                    d["_obj"] = o.obj
                    out.append(d)
                res = pc.ContinueRetrievePropertiesEx(res.token) if res.token else None
            return out
        finally:
            view.Destroy()

    def _datacenters(self):
        dcs = self._retrieve(vim.Datacenter, ["name", "vmFolder"])
        names = [d["name"] for d in dcs]
        if len(names) != len(set(names)):
            raise BackendError("Two datacenters share a name; this tool needs unique datacenter names.")
        return {d["name"]: d["vmFolder"] for d in dcs}

    # -- capture ------------------------------------------------------------
    def capture(self):
        dc_roots = self._datacenters()
        root_to_dc = {f._moId: n for n, f in dc_roots.items()}
        folders = {f["_obj"]._moId: f for f in self._retrieve(vim.Folder, ["name", "parent"])}
        vms = self._retrieve(vim.VirtualMachine, [
            "name", "parent", "runtime.powerState", "config.guestFullName", "config.uuid", "config.template"])

        cache = {}

        def locate(moid):
            """(datacenter, path-list) for a folder, or None if not under a VM root folder."""
            if moid in cache:
                return cache[moid]
            names, cur, result = [], moid, None
            while cur in folders:
                if cur in root_to_dc:
                    result = (root_to_dc[cur], list(reversed(names)))
                    break
                names.append(folders[cur]["name"])
                par = folders[cur].get("parent")
                cur = par._moId if par is not None else None
            cache[moid] = result
            return result

        out = {n: {"name": n, "folders": [], "vms": []} for n in sorted(dc_roots)}
        for moid in folders:
            loc = locate(moid)
            if loc and loc[1]:
                out[loc[0]]["folders"].append(loc[1])
        skipped = 0
        for v in vms:
            par = v.get("parent")
            loc = locate(par._moId) if par is not None and isinstance(par, vim.Folder) else None
            if not loc:
                skipped += 1  # e.g. inside a vApp, or orphaned
                continue
            out[loc[0]]["vms"].append({
                "name": v["name"], "path": loc[1], "power": str(v.get("runtime.powerState", "")),
                "guest": v.get("config.guestFullName", "") or "", "uuid": v.get("config.uuid", "") or "",
                "template": bool(v.get("config.template", False))})
        for d in out.values():
            d["folders"].sort(key=lambda p: (len(p), p))
        info = self.info()
        if skipped:
            info["warnings"].append("%d VM(s) inside vApps were not included in the folder inventory." % skipped)
        return new_inventory(info, [out[n] for n in sorted(out)])

    # -- live navigation (used right before every write) ---------------------
    def _folder(self, dc, path):
        roots = self._datacenters()
        if dc not in roots:
            raise BackendError("Datacenter '%s' does not exist on %s." % (dc, self.host))
        cur = roots[dc]
        for seg in path:
            nxt = [c for c in cur.childEntity if isinstance(c, vim.Folder) and c.name == seg]
            if not nxt:
                raise BackendError("Folder '%s' does not exist in datacenter %s." % ("/".join(path), dc))
            cur = nxt[0]
        return cur

    def folder_children(self, dc, parent_path):
        f = self._folder(dc, parent_path)
        return sorted(c.name for c in f.childEntity if isinstance(c, vim.Folder))

    def folder_is_empty(self, dc, path):
        return len(self._folder(dc, path).childEntity) == 0

    def _wait(self, task):
        while task.info.state in (vim.TaskInfo.State.queued, vim.TaskInfo.State.running):
            time.sleep(0.3)
        if task.info.state == vim.TaskInfo.State.error:
            raise BackendError(str(task.info.error.msg if hasattr(task.info.error, "msg") else task.info.error))

    # -- the only two write operations ---------------------------------------
    def create_folder(self, dc, parent_path, name):
        self._require_writes()
        parent = self._folder(dc, parent_path)
        if any(isinstance(c, vim.Folder) and c.name == name for c in parent.childEntity):
            raise FolderExists("A folder named '%s' already exists there." % name)
        try:
            parent.CreateFolder(name=name)
        except vim.fault.DuplicateName:
            raise FolderExists("A folder named '%s' already exists there." % name)
        except vim.fault.NoPermission as e:
            raise BackendError("Permission denied creating '%s'. The account needs the privilege "
                               "'Folder > Create folder' on the target (%s)." % (name, getattr(e, "privilegeId", "")))
        except vim.fault.InvalidName:
            raise BackendError("vCenter rejected the folder name '%s' as invalid." % name)

    def delete_empty_folder(self, dc, path):
        self._require_writes()
        f = self._folder(dc, path)
        if len(f.childEntity) != 0:
            raise BackendError("Folder is not empty; refusing to delete it.")
        self._wait(f.Destroy_Task())

    def close(self):
        try:
            Disconnect(self.si)
        except Exception:
            pass
