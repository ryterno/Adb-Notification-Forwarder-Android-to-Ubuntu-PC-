# Adb Notification Forwarder (Android to Ubuntu/Linux)

Forwards Android notifications to your Linux desktop using `adb` and D-Bus (`org.freedesktop.Notifications`).

## Features

- **New Notifications Only**: Ignores notifications already present on the phone at startup (`only_new_notifications = true`) and tracks seen notifications without re-triggering active ones.
- **Interactive Desktop Pop-ups**: Clicking a notification wakes up the Android screen and opens the corresponding app (`open_app_on_click = true`).
- **Dynamic App Icons**: Automatically fetches and caches app icons from Google Play via SerpApi (`fetch_icons = true`).
- **Smart Keyword Filtering**: Skips unwanted system or spam notifications using configurable `skip_keywords` (charging, USB status, etc.).
- **Package Filtering**: Optionally restrict forwarding to specific Android package names via `packages`.
- **Auto-Reconnect**: Supports USB ADB and optional TCP/IP ADB fallback (`address`, `port`, `auto_reconnect = true`).
- **Debug Mode**: Pass `-debug` for verbose logging.

---

## Requirements

- **System tools**: `adb` (`android-tools-adb`), Python 3, D-Bus, GLib/GObject (`python3-dbus`, `python3-gi` on Ubuntu/Debian)
- **Android**: USB debugging (or wireless ADB) enabled

---

## Installation & Usage

### 1. Install system & Python dependencies

On Ubuntu / Debian:
```bash
sudo apt install adb python3-dbus python3-gi
```

Or via `pip`:
```bash
pip3 install -r requirements.txt
```

### 2. Configure `config.ini`

Edit `config.ini` to customize:
- `interval`: polling interval in seconds (default: `10`)
- `only_new_notifications`: ignore existing notifications on startup (`true`/`false`, default: `true`)
- `open_app_on_click`: wake screen & launch app on click (`true`/`false`)
- `skip_keywords`: comma-separated keywords to ignore
- `packages`: comma-separated package names to allow (leave commented for all apps)
- `fetch_icons` / `serpapi_key`: automatic icon download from Google Play
- `address` / `port` / `auto_reconnect`: optional TCP/IP ADB fallback

### 3. Run

```bash
python3 server.py
```

With debug logs:
```bash
python3 server.py -debug
```

---

## Autostart with systemd (Ubuntu/Linux)

1. Build a standalone binary (optional, with PyInstaller):
   ```bash
   pyinstaller --onefile --noconsole server.py
   ```
2. Create `~/.config/systemd/user/adb-notification-forwarder.service`:
   ```ini
   [Unit]
   Description=Adb Notification Forwarder
   After=network.target dbus.service

   [Service]
   WorkingDirectory=/path/to/app
   ExecStart=/path/to/app/server
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=default.target
   ```
3. Enable and start the service:
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now adb-notification-forwarder.service
   ```

---

## License

Released under the MIT License. See [LICENSE.txt](LICENSE.txt) and [NOTICE.txt](NOTICE.txt).
