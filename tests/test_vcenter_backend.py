"""Exercise VCenterBackend's capture/path logic with a fake pyvmomi service (no vCenter needed)."""
import pytest

pyvmomi = pytest.importorskip("pyVmomi")
from pyVmomi import vim  # noqa: E402

from vcfa_folder_replication import vcenter  # noqa: E402
from vcfa_folder_replication.backends import BackendError  # noqa: E402


class Obj:
    def __init__(self, moid, cls):
        self._moId, self._cls = moid, cls


def fake_backend(monkeypatch):
    """Tree: DC1 / vm(root) / Prod / Web (VM web01 inside) ; hostFolder is a non-VM folder that must be ignored."""
    root, prod, web, hostf = Obj("f-root", vim.Folder), Obj("f-prod", vim.Folder), Obj("f-web", vim.Folder), Obj("f-host", vim.Folder)
    dc, vm = Obj("dc-1", vim.Datacenter), Obj("vm-1", vim.VirtualMachine)
    data = {
        vim.Datacenter: [{"name": "DC1", "vmFolder": root, "_obj": dc}],
        vim.Folder: [{"name": "vm", "parent": dc, "_obj": root}, {"name": "Prod", "parent": root, "_obj": prod},
                     {"name": "Web", "parent": prod, "_obj": web}, {"name": "host", "parent": dc, "_obj": hostf},
                     {"name": "Clusters", "parent": hostf, "_obj": Obj("f-cl", vim.Folder)}],
        vim.VirtualMachine: [{"name": "web01", "parent": web, "runtime.powerState": "poweredOn",
                              "config.guestFullName": "Linux", "config.uuid": "u1", "config.template": False, "_obj": vm}],
    }
    b = object.__new__(vcenter.VCenterBackend)
    b._writes, b.host = False, "fake"
    b._retrieve = lambda t, props: [dict(x) for x in data[t]]

    class About:
        version, build, fullName, name, apiType = "9.0.0", "1", "fake", "fake", "VirtualCenter"
    b.content = type("C", (), {"about": About})()
    return b


def test_capture_builds_vm_folder_paths(monkeypatch):
    monkeypatch.setattr(vcenter, "isinstance", lambda o, c: getattr(o, "_cls", None) is c or __builtins__["isinstance"](o, c), raising=False)
    b = fake_backend(monkeypatch)
    inv = b.capture()
    dc = inv["datacenters"][0]
    assert dc["name"] == "DC1"
    assert dc["folders"] == [["Prod"], ["Prod", "Web"]]          # host/Clusters ignored, root 'vm' excluded
    assert dc["vms"][0]["path"] == ["Prod", "Web"] and dc["vms"][0]["power"] == "poweredOn"
    assert inv["vcenter"]["version"] == "9.0.0" and inv["vcenter"]["warnings"] == []


def test_writes_are_guarded():
    b = object.__new__(vcenter.VCenterBackend)
    b._writes = False
    with pytest.raises(BackendError):
        b.create_folder("DC1", [], "x")
    with pytest.raises(BackendError):
        b.delete_empty_folder("DC1", ["x"])
