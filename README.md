# vcfa-folder-replication

Capture the **VM folder structure** of one vCenter and recreate it, one-to-one, on another.
Works with **vCenter Server 8.0 and 9.0** (either direction), on **Linux and Windows**, with a
point-and-click GUI, a scriptable CLI, and a **demo mode** that needs no vCenter at all.

```
 vCenter 8.0 (source) ──read-only──▶ capture ──▶ plan (diff) ──▶ create missing folders ──▶ vCenter 9.0 (target)
```

## Quick start

**Try the demo first (no vCenter needed):**

```bash
vcfa-folders gui --demo
```

A guided walkthrough explains each step beside the screen. Two simulated vCenters (8.0 source, 9.0 target)
are used, so you can safely press every button, including Undo.

### Install

| Platform | Command |
|---|---|
| Linux / macOS | `./install.sh` (needs Python 3.10+; no root; installs a private virtualenv and a `vcfa-folders` command) |
| Windows | `powershell -ExecutionPolicy Bypass -File .\install.ps1` (needs Python 3.10+ from python.org; creates Desktop and Start Menu shortcuts) |
| Any (pip) | `pip install .` |

Then run `vcfa-folders` (or use the Windows shortcut). It opens the GUI in your browser.

## How it works

1. **Connect** to the source and target vCenters (or load a previously saved capture file as the source).
2. **Capture** (read-only): every datacenter's VM folders and VMs. Export as `.json` (re-loadable) or `.csv` (Excel) - this doubles as an inventory report.
3. **Review the plan** (read-only): the target is re-read and each source folder is labelled:

   | Label | Meaning | What happens |
   |---|---|---|
   | `+` create | missing on target | will be created |
   | `=` exists | already there | nothing |
   | `!` conflict | target has the same name differing only by letter case (or an illegal name) | **skipped** - never renamed or merged |
   | `-` blocked | inside a conflict | skipped |
   | `x` datacenter missing | datacenter not on target | reported; **datacenters are never created** |
   | `.` only on target | extra folder on target | left completely alone |

4. **Replicate**: a dry run shows exactly what would happen, then you type `REPLICATE` to run it. Afterwards the plan re-checks the target so you can see everything now reads "exists".

## Safety design

- **Only one write operation is ever used: create folder.** Nothing is renamed, moved, merged or deleted; VMs are never touched.
- Backends are **read-only by default**; writes only work inside an explicit code path used by the apply step (enforced in code and covered by tests).
- **Dry run first**, and a typed confirmation for the real run (`--yes` on the CLI).
- The target is **re-read immediately before the run and before each create**; anything that appeared meanwhile is skipped, never duplicated. The run **stops at the first error**.
- Every run writes an **audit log** (`~/.vcfa-folder-replication/logs`, or `%USERPROFILE%\.vcfa-folder-replication\logs` on Windows).
- **Undo** removes only the folders that run created, and only if they are still empty.
- Re-running is safe (idempotent): already-existing folders are simply skipped.
- The GUI server listens on **127.0.0.1 only**, requires a random per-run key, and checks the Host header. Passwords stay in memory and are never logged or saved. TLS certificates are verified unless you tick *Accept self-signed certificate*.

### Recommended accounts

- **Source:** a read-only account (the built-in *Read-only* role is enough).
- **Target:** a dedicated account with only **Folder › Create folder** (and **Folder › Delete folder** if you want Undo) on the target datacenter's VM folder.

## Command line

```bash
vcfa-folders capture --host vc8.example.com --user svc-ro@vsphere.local -o vc8.capture.json   # or .csv
vcfa-folders plan  --source-file vc8.capture.json --target-host vc9.example.com --target-user svc-folders@vsphere.local
vcfa-folders apply --source-file vc8.capture.json --target-host vc9.example.com --target-user svc-folders@vsphere.local          # dry run
vcfa-folders apply --source-file vc8.capture.json --target-host vc9.example.com --target-user svc-folders@vsphere.local --yes     # real run
vcfa-folders rollback --log ~/.vcfa-folder-replication/logs/apply-<time>.json --target-host vc9.example.com --target-user ... --yes
```

Live source instead of a file: use `--source-host/--source-user`. Passwords are prompted for, or read from an environment variable named by `--source-password-env` / `--target-password-env`. Add `--source-insecure` / `--target-insecure` / `--insecure` for self-signed certificates. `--demo` works on `plan`, `apply` and `capture`.

## Scope and limits

- Replicates **folders** (the VM and Templates folder tree of each datacenter). The VM list is captured as inventory, but VMs are **not** moved or created.
- Datacenters must already exist on the target with the **same names** (create them first; this tool won't).
- VMs inside vApps are not listed in the inventory (a note is shown). Folder permissions, tags and custom attributes are not copied.
- Folder names are matched case-sensitively but a case-only difference is treated as a conflict, because vCenter will not allow both.

## Development

```bash
pip install -e ".[dev]"
python -m pytest
```

Layout: `model.py` (inventory format) · `backends.py` (interface + demo simulator) · `vcenter.py` (pyvmomi) · `planner.py` (pure diff) · `executor.py` (apply/rollback) · `server.py` + `web/` (GUI) · `cli.py`.

> **Status:** the core logic, GUI and CLI are covered by automated tests against the built-in simulator, and the
> vCenter backend is tested against a fake pyvmomi object model. It has **not yet been run against a real vCenter 8.0/9.0**
> - run the demo, then a `plan`/dry run against your lab first (both are read-only).
