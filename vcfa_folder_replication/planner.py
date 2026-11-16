"""Compare a source inventory with a target inventory. Pure function, no I/O.

Per source folder the outcome is exactly one of:

  create   - missing on the target; will be created
  exists   - already there (exact name match); nothing to do
  conflict - the target has a folder differing only by letter case, or the name is
             not legal; skipped (we never rename or merge)
  blocked  - its parent is a conflict/blocked, so it is skipped too

Datacenters missing on the target are reported ("missing_dc") and never created.
Target folders that the source does not have are listed as "extra" and never touched.
"""

from .model import MAX_NAME_LEN


def _lower(p):
    return tuple(s.lower() for s in p)


def build_plan(source, target):
    tgt = {d["name"]: d for d in target["datacenters"]}
    actions, dcs, extras = [], [], []
    counts = {"create": 0, "exists": 0, "conflict": 0, "blocked": 0, "missing_dc": 0, "extra": 0}

    for sdc in source["datacenters"]:
        name = sdc["name"]
        if name not in tgt:
            n = len(sdc["folders"])
            dcs.append({"name": name, "status": "missing_dc", "folders": n, "reason":
                        "Datacenter '%s' does not exist on the target. Datacenters are never created by "
                        "this tool - create it in vCenter first, then re-run the plan." % name})
            counts["missing_dc"] += 1
            continue
        dcs.append({"name": name, "status": "ok", "folders": len(sdc["folders"]), "reason": ""})
        exact = {tuple(p) for p in tgt[name]["folders"]}
        by_lower = {}
        for p in exact:
            by_lower.setdefault(_lower(p), p)
        status = {}
        for p in sorted((tuple(f) for f in sdc["folders"]), key=lambda f: (len(f), [s.lower() for s in f])):
            reason = ""
            parent = p[:-1]
            if parent and status.get(parent) in ("conflict", "blocked"):
                st, reason = "blocked", "Skipped because its parent folder '%s' is %s." % (
                    "/".join(parent), status[parent])
            elif len(p[-1]) > MAX_NAME_LEN or not p[-1].strip():
                st, reason = "conflict", "Folder name is empty or longer than %d characters." % MAX_NAME_LEN
            elif p in exact:
                st = "exists"
            elif _lower(p) in by_lower:
                st = "conflict"
                reason = "Target already has '%s', which differs only by letter case." % "/".join(by_lower[_lower(p)])
            else:
                st = "create"
            status[p] = st
            counts[st] += 1
            actions.append({"dc": name, "path": list(p), "status": st, "reason": reason})
        source_lower = {_lower(tuple(f)) for f in sdc["folders"]}
        for p in sorted(exact, key=lambda f: (len(f), f)):
            if _lower(p) not in source_lower:
                extras.append({"dc": name, "path": list(p)})
                counts["extra"] += 1

    return {"actions": actions, "datacenters": dcs, "extras": extras, "summary": counts,
            "source_host": source["vcenter"].get("host"), "target_host": target["vcenter"].get("host")}
