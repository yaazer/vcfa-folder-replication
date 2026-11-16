#!/usr/bin/env bash
# Installs vcfa-folder-replication for the current user on Linux (and macOS).
# No root needed. Creates a private virtualenv and a `vcfa-folders` launcher.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="${XDG_DATA_HOME:-$HOME/.local/share}/vcfa-folder-replication"
BIN="$HOME/.local/bin"

PY="$(command -v python3 || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Python 3.10 or newer is required. Install it first, e.g.:"
  echo "  Debian/Ubuntu: sudo apt install python3 python3-venv"
  echo "  RHEL/Fedora:   sudo dnf install python3"
  exit 1
fi
if ! "$PY" -c 'import venv, ensurepip' 2>/dev/null; then
  echo "The python3 'venv' module is missing. On Debian/Ubuntu: sudo apt install python3-venv"
  exit 1
fi

echo "Installing into $APP ..."
"$PY" -m venv "$APP/venv"
"$APP/venv/bin/pip" install --quiet --upgrade pip
"$APP/venv/bin/pip" install --quiet "$HERE"

mkdir -p "$BIN"
ln -sf "$APP/venv/bin/vcfa-folders" "$BIN/vcfa-folders"

# Optional application-menu entry
DESK="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$DESK"
cat > "$DESK/vcfa-folder-replication.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=vCenter Folder Replication
Comment=Copy VM folder structure between vCenters
Exec=$APP/venv/bin/vcfa-folders gui
Terminal=true
Categories=System;Utility;
DESKTOP

echo
echo "Done. Start it with:   vcfa-folders        (opens the GUI in your browser)"
echo "Try it without a vCenter:   vcfa-folders gui --demo"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "NOTE: add $BIN to your PATH (e.g. echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc)";; esac
echo "To uninstall:          rm -rf '$APP' '$BIN/vcfa-folders' '$DESK/vcfa-folder-replication.desktop'"
