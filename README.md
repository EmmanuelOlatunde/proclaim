# ChurchCast — Local Church Presentation System

A local-first, LAN-based presentation and lyrics-casting web application for church services.
No cloud, no Internet required during live use.

## Architecture

```
PHONE (controller) → Wi-Fi → LAPTOP (server + display) → vMix → projector/stream
```

- **Laptop** runs the Django server and displays the presentation in a browser.
- **Phone** opens the control page and sends commands over local WebSocket.
- **vMix** captures the laptop's display browser as a Browser/Web source.
- All data stays on the local network. No cloud realtime, no cloud storage.

## Quick Start

```bash
./start.sh
# or: python manage.py runserver 0.0.0.0:3000 --noreload
```

Then:
- On the **phone**: open `http://LAPTOP-IP:3000/control`
- On the **laptop**: open `http://LAPTOP-IP:3000/display/CHURCH1`
- In **vMix**: add the display URL as a Browser source

## Firewall

The server listens on `0.0.0.0:3000` (all interfaces).
Allow inbound TCP port 3000 from the local network:

**Windows:**
```powershell
netsh advfirewall firewall add rule name="ChurchCast" dir=in action=allow protocol=TCP localport=3000
```

**Linux (ufw):**
```bash
sudo ufw allow 3000/tcp
```

**Linux (firewalld):**
```bash
sudo firewall-cmd --add-port=3000/tcp --permanent
sudo firewall-cmd --reload
```

Do NOT expose port 3000 to the public Internet. The server is intended for LAN use only.

## Features

- **Songs**: create, edit, delete, search; section-based navigation (Verse/Chorus/Bridge)
- **Song Import**: paste plain text or ChordPro lyrics; auto-parses into sections
- **Scripture**: bundled KJV passages; parse references like `John 3:16`, `Genesis 1:1-5`, `Psalm 23`
- **Images**: upload from laptop, browse, search, present full-canvas
- **Announcements**: title + body text slides
- **Countdown Timer**: timestamp-synced (no per-second network traffic)
- **Blank Screen**: black out display while preserving position
- **Service Queue**: order items, reorder, jump to any item
- **Live Preview**: control page shows what's currently displayed
- **Reconnection**: auto-reconnect WebSocket, state restore on reconnect
- **Offline**: works without Internet — all data local (SQLite + local filesystem)

## Network Efficiency

- Slide changes send only small state messages over WebSocket
- No polling, no per-second countdown messages
- Images loaded once and cached by the browser
- Song content sent once when presented; only slide index changes on next/prev

## Data

All data stored in local SQLite (`db.sqlite3`):
- Songs, song sections
- Announcements
- Uploaded images (local filesystem under `media/`)
- Rooms and presentation state
- Service queue
