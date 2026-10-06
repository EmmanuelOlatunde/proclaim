#!/usr/bin/env bash
# Proclaim — start script
# Starts the local presentation server on the LAN.
# Usage: ./start.sh [port]
set -e

PORT="${1:-3000}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python"

# segno renders the connection QR codes entirely offline (pure-Python PNG
# writer); psutil ranks the usable network interfaces. Both are runtime
# requirements, so they must be installed here as well as in build_windows.bat.
DEPS="Django channels daphne Pillow segno psutil"

if [ ! -f "$PYTHON" ]; then
    echo "Creating virtual environment and installing dependencies..."
    python3 -m venv "$SCRIPT_DIR/.venv"
    "$PYTHON" -m pip install --quiet --upgrade pip
    "$PYTHON" -m pip install --quiet $DEPS
else
    # An existing venv predating the QR/network work would start and then fail
    # on the first scan, so top up quietly rather than only on first create.
    if ! "$PYTHON" -c "import segno, psutil" >/dev/null 2>&1; then
        echo "Installing newly added dependencies (segno, psutil)..."
        "$PYTHON" -m pip install --quiet $DEPS
    fi
fi

# The QR code and the address list are rendered from this, so it has to agree
# with the port the server actually binds to.
export PROCLAIM_PORT="$PORT"

echo "============================================"
echo "  Proclaim — Local Presentation Server"
echo "============================================"
echo ""

# Detect LAN IP (no Internet required). Show every plausible address rather than
# guessing one: a laptop with both Wi-Fi and a wired cable, or a phone hotspot on
# a different adapter than the default route, needs the operator to pick.
LAN_IP=""
if command -v hostname >/dev/null 2>&1; then
    LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
fi
[ -z "$LAN_IP" ] && LAN_IP="YOUR-LAPTOP-IP"

echo "  Control (phone):  http://$LAN_IP:$PORT/control"
echo "  Display (laptop): http://$LAN_IP:$PORT/display/CHURCH1"
echo ""
echo "  Open http://127.0.0.1:$PORT/ on the laptop for a QR code your phone can"
echo "  scan, and to see every network address this machine has."
echo ""
echo "  Press Ctrl+C to stop."
echo "============================================"
echo ""

exec "$PYTHON" manage.py runserver 0.0.0.0:"$PORT" --noreload