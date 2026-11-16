import pytest

from vcfa_folder_replication import executor
from vcfa_folder_replication.backends import BackendError, DemoBackend
from vcfa_folder_replication.model import InventoryError, summarize, to_csv, validate_inventory
from vcfa_folder_replication.planner import build_plan


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("VCFA_HOME", str(tmp_path))


def pair():
    s, t = DemoBackend("source", delay=0), DemoBackend("target", delay=0)
    return s, t


def test_plan_covers_every_outcome():
    s, t = pair()
    plan = build_plan(s.capture(), t.capture())
    by = {(a["dc"], "/".join(a["path"])): a for a in plan["actions"]}
    assert by[("DC-East", "Production")]["status"] == "exists"
    assert by[("DC-East", "Production/App")]["status"] == "create"
    assert by[("DC-East", "Development")]["status"] == "conflict"       # target has 'development'
    assert by[("DC-East", "Development/Web")]["status"] == "blocked"
    assert any(d["name"] == "DC-Branch" and d["status"] == "missing_dc" for d in plan["datacenters"])
    assert [e["path"] for e in plan["extras"]] == [["Lab-Sandbox"], ["development"]] or plan["summary"]["extra"] >= 1
    assert plan["summary"]["create"] > 10


def test_dry_run_writes_nothing():
    s, t = pair()
    before = t.capture()
    run = executor.execute(build_plan(s.capture(), t.capture()), t, dry_run=True)
    assert run["counts"]["would_create"] > 0 and run["counts"]["created"] == 0
    assert t.capture()["datacenters"] == before["datacenters"]


def test_apply_is_idempotent_and_replicates():
    s, t = pair()
    run = executor.execute(build_plan(s.capture(), t.capture()), t, dry_run=False)
    assert run["ok"] and run["counts"]["created"] > 0
    again = build_plan(s.capture(), t.capture())
    assert again["summary"]["create"] == 0
    run2 = executor.execute(again, t, dry_run=False)
    assert run2["counts"]["created"] == 0
    # conflict & blocked folders were never created, existing target-only folders untouched
    names = {tuple(f) for f in [x for d in t.capture()["datacenters"] if d["name"] == "DC-East" for x in d["folders"]]}
    assert ("development",) in names and ("Lab-Sandbox",) in names and ("Development",) not in names


def test_writes_blocked_outside_executor():
    _, t = pair()
    with pytest.raises(BackendError):
        t.create_folder("DC-East", [], "Nope")


def test_skips_folder_that_appeared_after_planning():
    s, t = pair()
    plan = build_plan(s.capture(), t.capture())
    with t.writes_enabled():
        t.create_folder("DC-East", [], "Infrastructure")  # someone else creates it meanwhile
    run = executor.execute(plan, t, dry_run=False)
    assert any(e["result"] == "skipped" for e in run["entries"])
    assert run["ok"]


def test_rollback_removes_only_empty_created_folders():
    s, t = pair()
    run = executor.execute(build_plan(s.capture(), t.capture()), t, dry_run=False)
    t.dcs["DC-East"]["vms"].append({"name": "x", "path": ["Production", "App"]})  # now occupied
    rb = executor.rollback(run, t)
    assert rb["counts"]["kept"] == 1 and rb["counts"]["removed"] > 0
    left = {tuple(f) for f in t.dcs["DC-East"]["folders"]}
    assert ("Production", "App") in left and ("Production", "Web") in left  # pre-existing + kept
    assert ("Production", "Database") not in left


def test_validate_and_csv():
    s, _ = pair()
    inv = s.capture()
    assert validate_inventory(inv) is inv
    assert summarize(inv)["folders"] > 20
    assert to_csv(inv).startswith("datacenter,type,path")
    with pytest.raises(InventoryError):
        validate_inventory({"schema": 99})
    with pytest.raises(InventoryError):
        validate_inventory({"schema": 1, "vcenter": {}, "datacenters": [{"name": "a", "folders": [["ok", ""]]}]})
