#!/usr/bin/env bash
# ChurchCast — start script
# Starts the local presentation server on the LAN.
# Usage: ./start.sh [port]
set -e

PORT="${1:-3000}"

# Resolve venv
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "Creating virtual environment..."
    python3 -m venv --system-site-packages "$SCRIPT_DIR/.venv"
    python3 -m pip install --target "$SCRIPT_DIR/.venv/lib/python3.13/site-packages" --quiet pip
    "$PYTHON" -m pip install --quiet Django channels daphne Pillow
fi

echo "============================================"
echo "  ChurchCast — Local Presentation Server"
echo "============================================"
echo ""

# Detect LAN IP
if command -v hostname &>/dev/null; then
    LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
fi
if [ -z "$LAN_IP" ]; then
    LAN_IP="YOUR-LAPTOP-IP"
fi

echo "  Control (phone):  http://$LAN_IP:$PORT/control"
echo "  Display (laptop): http://$LAN_IP:$PORT/display/CHURCH1"
echo ""
echo "  Press Ctrl+C to stop."
echo "============================================"
echo ""

exec "$PYTHON" manage.py runserver 0.0.0.0:"$PORT" --noreload
