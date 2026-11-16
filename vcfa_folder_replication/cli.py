"""Command line interface. `vcfa-folders` with no arguments opens the GUI."""

import argparse
import getpass
import json
import os
import sys

from . import __version__, executor
from .backends import BackendError, DemoBackend
from .model import InventoryError, path_str, summarize, to_csv, validate_inventory
from .planner import build_plan

SYMBOL = {"create": "[+]", "exists": "[=]", "conflict": "[!]", "blocked": "[-]"}


def add_endpoint(p, prefix, label):
    h = "--%shost" % prefix
    p.add_argument(h, help="%s vCenter hostname or IP" % label)
    p.add_argument("--%suser" % prefix, help="%s vCenter username" % label)
    p.add_argument("--%sport" % prefix, type=int, default=443)
    p.add_argument("--%spassword-env" % prefix, metavar="VAR",
                   help="read the password from this environment variable (otherwise you are prompted)")
    p.add_argument("--%sinsecure" % prefix, action="store_true",
                   help="accept a self-signed TLS certificate")


def connect(args, prefix, label):
    from .vcenter import VCenterBackend
    g = lambda k: getattr(args, prefix.replace("-", "_") + k)
    if not g("host") or not g("user"):
        raise SystemExit("error: --%shost and --%suser are required for %s." % (prefix, prefix, label))
    env = g("password_env")
    pw = os.environ.get(env, "") if env else getpass.getpass("%s password for %s@%s: " % (label, g("user"), g("host")))
    if not pw:
        raise SystemExit("error: no password supplied.")
    return VCenterBackend(g("host"), g("user"), pw, port=g("port"), insecure=g("insecure"))


def load_file(path):
    with open(path, encoding="utf-8") as f:
        return validate_inventory(json.load(f))


def source_inventory(args):
    if args.demo:
        return DemoBackend("source", delay=0).capture()
    if args.source_file:
        return load_file(args.source_file)
    b = connect(args, "source-", "Source")
    try:
        return b.capture()
    finally:
        b.close()


def target_backend(args):
    return DemoBackend("target", delay=0) if args.demo else connect(args, "target-", "Target")


def print_plan(plan):
    s = plan["summary"]
    print("\nPlan: %s  ->  %s" % (plan["source_host"], plan["target_host"]))
    for d in plan["datacenters"]:
        if d["status"] == "missing_dc":
            print("[x] Datacenter %s: %s" % (d["name"], d["reason"]))
    for a in plan["actions"]:
        line = "%s %s/%s" % (SYMBOL[a["status"]], a["dc"], path_str(a["path"]))
        print(line + ("   <- " + a["reason"] if a["reason"] else ""))
    for e in plan["extras"]:
        print("[ ] (only on target, left alone) %s/%s" % (e["dc"], path_str(e["path"])))
    print("\n%d to create, %d already exist, %d conflicts, %d blocked, %d datacenters missing on target, "
          "%d target-only folders." % (s["create"], s["exists"], s["conflict"], s["blocked"], s["missing_dc"], s["extra"]))


def cmd_capture(a):
    if a.demo:
        inv = DemoBackend(a.demo_role, delay=0).capture()
    else:
        b = connect(a, "", "vCenter")
        try:
            inv = b.capture()
        finally:
            b.close()
    out = to_csv(inv) if a.output.endswith(".csv") else json.dumps(inv, indent=2)
    with open(a.output, "w", encoding="utf-8") as f:
        f.write(out)
    s = summarize(inv)
    print("Captured %d datacenters, %d folders, %d VMs from %s -> %s" % (
        s["datacenters"], s["folders"], s["vms"], inv["vcenter"].get("host"), a.output))


def cmd_plan(a):
    tgt = target_backend(a)
    try:
        print_plan(build_plan(source_inventory(a), tgt.capture()))
    finally:
        tgt.close()


def cmd_apply(a):
    src = source_inventory(a)
    tgt = target_backend(a)
    try:
        plan = build_plan(src, tgt.capture())
        print_plan(plan)
        n = plan["summary"]["create"]
        if not n:
            return print("\nNothing to do.")
        if not a.yes:
            return print("\nDRY RUN - nothing was changed. Re-run with --yes to create these %d folder(s)." % n)
        print("\nCreating %d folder(s). Nothing will be renamed, moved or deleted." % n)
        run = executor.execute(plan, tgt, dry_run=False,
                               progress=lambda d, t, e: print("  [%d/%d] %s" % (d, t, e["message"])))
        print("\nDone: %(created)d created, %(skipped)d skipped, %(failed)d failed." % run["counts"])
        print("Audit log (use it with the 'rollback' command): %s" % run["log_file"])
        if not run["ok"]:
            sys.exit(1)
    finally:
        tgt.close()


def cmd_rollback(a):
    with open(a.log, encoding="utf-8") as f:
        run = json.load(f)
    todo = [e for e in run.get("entries", []) if e.get("result") == "created"]
    if not todo:
        return print("That log created no folders; nothing to undo.")
    print("Would remove up to %d empty folder(s) created by that run:" % len(todo))
    for e in todo:
        print("  %s/%s" % (e["dc"], path_str(e["path"])))
    if not a.yes:
        return print("\nDRY RUN. Re-run with --yes to remove them (non-empty folders are always kept).")
    tgt = target_backend(a)
    try:
        out = executor.rollback(run, tgt, progress=lambda d, t, e: print("  [%d/%d] %s" % (d, t, e["message"])))
        print("\n%(removed)d removed, %(kept)d kept, %(gone)d already gone, %(failed)d failed." % out["counts"])
    finally:
        tgt.close()


def cmd_gui(a):
    from .server import serve
    serve(port=a.port, demo=a.demo, open_browser=not a.no_browser)


def main(argv=None):
    p = argparse.ArgumentParser(prog="vcfa-folders", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    g = sub.add_parser("gui", help="open the graphical interface (default)")
    g.add_argument("--demo", action="store_true", help="start in demo mode (no vCenter needed)")
    g.add_argument("--port", type=int, default=0)
    g.add_argument("--no-browser", action="store_true", help="print the link instead of opening a browser")
    g.set_defaults(fn=cmd_gui)

    c = sub.add_parser("capture", help="read-only: save a vCenter's VM folders and VMs to a file (.json or .csv)")
    add_endpoint(c, "", "vCenter")
    c.add_argument("-o", "--output", required=True)
    c.add_argument("--demo", action="store_true")
    c.add_argument("--demo-role", choices=["source", "target"], default="source", help=argparse.SUPPRESS)
    c.set_defaults(fn=cmd_capture)

    for name, fn, hlp in (("plan", cmd_plan, "read-only: show what would be created"),
                          ("apply", cmd_apply, "create missing folders (dry run unless --yes)")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("--source-file", help="a capture file made by 'capture'")
        add_endpoint(s, "source-", "Source")
        add_endpoint(s, "target-", "Target")
        s.add_argument("--demo", action="store_true", help="use the built-in simulated vCenters")
        if name == "apply":
            s.add_argument("--yes", action="store_true", help="actually create the folders")
        s.set_defaults(fn=fn)

    r = sub.add_parser("rollback", help="remove the (still empty) folders a previous apply created")
    r.add_argument("--log", required=True, help="the audit log written by apply")
    add_endpoint(r, "target-", "Target")
    r.add_argument("--demo", action="store_true")
    r.add_argument("--yes", action="store_true")
    r.set_defaults(fn=cmd_rollback)

    args = p.parse_args(argv)
    if not args.cmd:
        args = p.parse_args(["gui"])
    try:
        args.fn(args)
    except (BackendError, InventoryError, OSError, json.JSONDecodeError) as e:
        print("error:", e, file=sys.stderr)
        return 1
    return 0


def gui_main():
    raise SystemExit(main(["gui"] + sys.argv[1:]))
