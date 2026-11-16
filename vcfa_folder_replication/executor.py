"""Apply a plan to a target backend, carefully.

Guarantees:
  * Only plan items with status "create" are ever acted on, parents before children.
  * Immediately before each create, the target is re-read live. If the folder
    appeared since the plan was made, it is skipped, never duplicated or replaced.
  * The only write operation used is "create folder". Nothing is renamed,
    moved, or deleted, and no VM is touched.
  * Dry-run (the default) performs zero writes.
  * An audit log of exactly what this run created is written to disk (also on
    failure), and is what ``rollback`` uses.
"""

import json
import os
from pathlib import Path

from .backends import BackendError, FolderExists
from .model import now_iso, path_str


def log_dir():
    d = Path(os.environ.get("VCFA_HOME", Path.home() / ".vcfa-folder-replication")) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_log(run, name):
    p = log_dir() / name
    p.write_text(json.dumps(run, indent=2), encoding="utf-8")
    return str(p)


def execute(plan, backend, dry_run=True, progress=None, should_stop=lambda: False):
    todo = [a for a in plan["actions"] if a["status"] == "create"]
    todo.sort(key=lambda a: len(a["path"]))  # parents first
    run = {"kind": "apply", "dry_run": dry_run, "demo": bool(backend.is_demo), "started": now_iso(),
           "target": backend.info(), "entries": [], "ok": True, "aborted": ""}

    def note(entry):
        entry["time"] = now_iso()
        run["entries"].append(entry)
        if progress:
            progress(len(run["entries"]), len(todo), entry)

    try:
        for a in todo:
            if should_stop():
                run["aborted"] = "Stopped by user."
                break
            dc, path = a["dc"], a["path"]
            base = {"dc": dc, "path": path}
            if dry_run:
                note({**base, "result": "would_create", "message": "Would create %s/%s" % (dc, path_str(path))})
                continue
            try:
                siblings = backend.folder_children(dc, path[:-1])
            except BackendError as e:
                note({**base, "result": "failed", "message": str(e)})
                run["ok"], run["aborted"] = False, "Stopped at first error: " + str(e)
                break
            if path[-1] in siblings:
                note({**base, "result": "skipped", "message": "Already exists now (created since the plan was made)."})
                continue
            if any(s.lower() == path[-1].lower() for s in siblings):
                note({**base, "result": "skipped", "message": "A folder differing only by case exists; skipped."})
                continue
            try:
                with backend.writes_enabled():
                    backend.create_folder(dc, path[:-1], path[-1])
                note({**base, "result": "created", "message": "Created %s/%s" % (dc, path_str(path))})
            except FolderExists:
                note({**base, "result": "skipped", "message": "Already exists."})
            except BackendError as e:
                note({**base, "result": "failed", "message": str(e)})
                run["ok"], run["aborted"] = False, "Stopped at first error: " + str(e)
                break
    finally:
        run["finished"] = now_iso()
        stamp = run["started"].replace(":", "").replace("-", "")[:15]
        prefix = "dryrun" if dry_run else "apply"
        if not dry_run or run["entries"]:
            run["log_file"] = write_log(run, "%s-%s%s.json" % (prefix, "demo-" if backend.is_demo else "", stamp))
    run["counts"] = {k: sum(1 for e in run["entries"] if e["result"] == k)
                     for k in ("created", "skipped", "failed", "would_create")}
    return run


def rollback(run_log, backend, progress=None):
    """Delete the folders a previous run created - but only those, and only if still empty."""
    created = [e for e in run_log.get("entries", []) if e.get("result") == "created"]
    created.sort(key=lambda e: -len(e["path"]))  # children first
    out = {"kind": "rollback", "started": now_iso(), "entries": [], "ok": True}
    for e in created:
        dc, path = e["dc"], e["path"]
        base = {"dc": dc, "path": path}
        try:
            if path[-1] not in backend.folder_children(dc, path[:-1]):
                r = {**base, "result": "gone", "message": "Already gone."}
            elif not backend.folder_is_empty(dc, path):
                r = {**base, "result": "kept", "message": "Kept: it now contains items, so it was left alone."}
            else:
                with backend.writes_enabled():
                    backend.delete_empty_folder(dc, path)
                r = {**base, "result": "removed", "message": "Removed empty folder %s/%s" % (dc, path_str(path))}
        except BackendError as ex:
            r = {**base, "result": "failed", "message": str(ex)}
            out["ok"] = False
        out["entries"].append(r)
        if progress:
            progress(len(out["entries"]), len(created), r)
    out["finished"] = now_iso()
    out["counts"] = {k: sum(1 for x in out["entries"] if x["result"] == k)
                     for k in ("removed", "kept", "gone", "failed")}
    out["log_file"] = write_log(out, "rollback-%s.json" % out["started"].replace(":", "").replace("-", "")[:15])
    return out
