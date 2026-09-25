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

`start.sh` prints your detected LAN address automatically. `create_room` on
open is automatic — any room code creates its room on first use.

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
- **Scripture**: the complete offline KJV is bundled (66 books, 31,102 verses,
  `presentation/data/kjv.json`). References are parsed flexibly:
  `John 3:16`, `Genesis 1:1-5`, `Psalm 23` / `Psalms 23`, `1 John 1:9`,
  `Song of Solomon 2:1`, abbreviations like `Jn 3:16`. No Internet needed.
- **Images**: upload from laptop, browse, search, present full-canvas
- **Announcements**: title + body text slides
- **Countdown Timer**: timestamp-synced (no per-second network traffic)
- **Blank Screen**: black out display while preserving position
- **Service Queue**: order items, reorder, jump to any item
- **Live Preview**: control page shows what's currently displayed
- **Reconnection**: auto-reconnect WebSocket, state restore on reconnect
- **Offline**: works without Internet — all data local (SQLite + local filesystem)

## Network Efficiency

Three small message types keep slide changes cheap:

- `{"type": "state", ...}` — full snapshot. Sent once when content changes
  (present/queue-jump/clear/stop) and on (re)connect.
- `{"type": "slide_change", "contentType", "itemId", "slideIndex", "slideCount"}`
  — minimal navigation for next/prev/goto_slide. No content is retransmitted.
- `{"type": "blank", "blank": bool}` — minimal blank toggle; position preserved.

Countdowns sync by absolute `endTime` only — no per-second messages.
No polling anywhere. Images load once and are cached by the browser.

## Tests

```bash
.venv/bin/python manage.py test presentation
```

Covers scripture parsing (multi-word/numbered books), plain-text/ChordPro song
import, and the full WebSocket protocol (present → minimal `slide_change` →
`blank` → reconnection snapshot) via `channels.testing.WebsocketCommunicator`.

## Data

All data stored in local SQLite (`db.sqlite3`):
- Songs, song sections
- Announcements
- Uploaded images (local filesystem under `media/`)
- Rooms and presentation state
- Service queue
