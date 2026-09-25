#!/usr/bin/env bash
# Proclaim — start script
# Starts the local presentation server on the LAN.
# Usage: ./start.sh [port]
set -e

PORT="${1:-3000}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "Creating virtual environment and installing dependencies..."
    python3 -m venv "$SCRIPT_DIR/.venv"
    "$PYTHON" -m pip install --quiet --upgrade pip
    "$PYTHON" -m pip install --quiet Django channels daphne Pillow
fi

echo "============================================"
echo "  Proclaim — Local Presentation Server"
echo "============================================"
echo ""

# Detect LAN IP (no Internet required)
LAN_IP=""
if command -v hostname >/dev/null 2>&1; then
    LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
fi
[ -z "$LAN_IP" ] && LAN_IP="YOUR-LAPTOP-IP"

echo "  Control (phone):  http://$LAN_IP:$PORT/control"
echo "  Display (laptop): http://$LAN_IP:$PORT/display/CHURCH1"
echo ""
echo "  Press Ctrl+C to stop."
echo "============================================"
echo ""

exec "$PYTHON" manage.py runserver 0.0.0.0:"$PORT" --noreload