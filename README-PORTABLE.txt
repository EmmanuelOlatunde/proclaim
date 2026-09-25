=================================================
PROCLAIM PORTABLE
=================================================

Proclaim is a church presentation system that runs on your
local network only. Everything lives in this one folder.
No installation is needed.

WHAT YOU NEED
=================================================
  * A Windows 10 or Windows 11 computer (64-bit).
  * Nothing else. No Python, no pip, no Node.js, no Git,
    no developer tools.

HOW TO USE
=================================================
  1. Copy the ENTIRE Proclaim folder to the computer you
     will present from. The desktop, an internal drive, or
     a USB stick all work.
  2. Open the Proclaim folder.
  3. Double-click Proclaim.exe.
  4. A console window opens and Proclaim starts. Wait until
     it shows "Server: RUNNING".
  5. On the CONTROL device (phone or laptop on the same
     Wi-Fi/LAN), open:
        http://IP:3000/control
     use the IP shown inside Proclaim's console window.
  6. On the DISPLAY computer / projector source (same LAN),
     open:
        http://IP:3000/display/CHURCH1
  7. To stop Proclaim: press Ctrl+C in the console window,
     or close that window.

KEEP THE FOLDER TOGETHER
=================================================
  Proclaim.exe, the _internal folder, and the data and
  media folders must all stay together inside the Proclaim
  folder. If you move Proclaim to another computer, move
  the whole folder. Do not move Proclaim.exe out of it.

YOUR DATA
=================================================
  * Songs and presentations are stored in:
      data\proclaim.sqlite3
  * Every computer creates its own data on first launch, so
    each computer keeps its own content.
  * Pictures you upload are stored in the media folder.
  * future hymn data will live in: data\hymns

FIREWALL (OPTIONAL)
=================================================
  The first time you run Proclaim, Windows may ask whether
  to allow it through the firewall. Choose "Private
  networks" so phones and other computers on your church
  LAN can connect.

  Proclaim listens on TCP port 3000. To add the rule
  manually, open an Administrator command prompt and run:

      netsh advfirewall firewall add rule name="Proclaim" dir=in action=allow protocol=TCP localport=3000

  Do NOT expose Proclaim to the public Internet. It is for
  LAN use only.

IF PROCLAIM WILL NOT START
=================================================
  The console window stays open and shows:
      PROCLAIM STARTUP ERROR
  Keep that window open and show it to whoever provided
  Proclaim. The window contains the reason it could not
  start.