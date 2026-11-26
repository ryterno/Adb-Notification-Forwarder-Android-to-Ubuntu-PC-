import time
import os
import sys
import logging
import configparser
import subprocess
import threading
import urllib.request
import json
import re
import dbus
import dbus.mainloop.glib
from gi.repository import GLib

# Gestion du mode debug via l'argument -debug
DEBUG_MODE = "-debug" in sys.argv

# Configuration des logs selon le mode debug
log_level = logging.INFO if DEBUG_MODE else logging.WARNING
logging.basicConfig(level=log_level, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('main')

# Répertoire de base (compatible script et exécutable PyInstaller)
if getattr(sys, 'frozen', False):
	BASE_DIR = os.path.dirname(sys.executable)
else:
	BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Dictionnaire global pour lier l'ID de notification au package de l'application
active_notifications = {}
active_notifications_lock = threading.Lock()
MAX_ACTIVE_NOTIFICATIONS = 500
MAX_VISITED_NOTIFICATIONS = 2000

# Paramètres globaux modifiables via config.ini
packages = set()
interval = 10
privkey = os.path.join(os.path.expanduser("~"), ".android", "adbkey")
pubkey = privkey + ".pub"
ICONS_DIR = os.path.join(BASE_DIR, "icons")

SERPAPI_KEY = ""
FETCH_ICONS = True
OPEN_APP_ON_CLICK = True
ONLY_NEW_NOTIFICATIONS = True
AUTO_RECONNECT = True
ADB_ADDRESS = ""
ADB_PORT = 5555
SKIP_KEYWORDS = ["charge", "chargement", "usb"]

# Expressions régulières pour le parsing des notifications
exp_start = re.compile(r"^(\s*)NotificationRecord\b.*")
exp_title = re.compile(r"^\s*android\.title=[^\(]*\((.*)\)\s*$")
exp_text = re.compile(r"^\s*android\.text=[^\(]*\((.*)\)\s*$")
exp_big_text = re.compile(r"^\s*android\.bigText=[^\(]*\((.*)\)\s*$")
exp_time = re.compile(r"^\s*mUpdateTimeMs=(\d+)")
exp_pkg = re.compile(r"pkg=([^\s\)]+)")


def _finalize_notification(current, ret):
	if not current:
		return
	if "time" not in current:
		current["time"] = -1
	if not current.get("text") and current.get("big_text"):
		current["text"] = current["big_text"]
	if "title" in current or "text" in current:
		if "title" not in current:
			current["title"] = current.get("pkg") or "Notification"
		if "text" not in current:
			current["text"] = ""
		current.pop("big_text", None)
		ret.append(current)


def parse_notifications(result):
	ret = []
	if not result:
		return ret

	entry = False
	record_indent = 0
	current = {}

	for line in result.splitlines():
		start_match = exp_start.match(line)
		if start_match:
			if entry:
				_finalize_notification(current, ret)
			entry = True
			record_indent = len(start_match.group(1))
			pkg_match = exp_pkg.search(line)
			current = {"pkg": pkg_match.group(1) if pkg_match else None}
			continue

		if not entry:
			continue

		stripped = line.lstrip()
		if stripped and (len(line) - len(stripped)) <= record_indent:
			_finalize_notification(current, ret)
			entry = False
			current = {}
			continue

		match_title = exp_title.match(line)
		if match_title:
			current["title"] = match_title.group(1)
			continue

		match_text = exp_text.match(line)
		if match_text:
			current["text"] = match_text.group(1)
			continue

		match_big_text = exp_big_text.match(line)
		if match_big_text:
			current["big_text"] = match_big_text.group(1)
			continue

		match_time = exp_time.match(line)
		if match_time:
			current["time"] = int(match_time.group(1))

	if entry:
		_finalize_notification(current, ret)

	return ret


def fetch_icon_via_serpapi(pkg, icon_path):
	if not FETCH_ICONS or not SERPAPI_KEY or not pkg or pkg == "unknown":
		return
	try:
		if DEBUG_MODE:
			logger.info(f"[SERPAPI] Récupération de l'icône pour {pkg}...")
		url = f"https://serpapi.com/search?engine=google_play_product&product_id={pkg}&store=apps&api_key={SERPAPI_KEY}"
		req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
		with urllib.request.urlopen(req, timeout=5) as response:
			data = json.loads(response.read().decode())
			icon_url = None
			if "product_info" in data and "thumbnail" in data["product_info"]:
				icon_url = data["product_info"]["thumbnail"]
			elif "icon" in data:
				icon_url = data["icon"]

			if icon_url:
				icon_req = urllib.request.Request(icon_url, headers={'User-Agent': 'Mozilla/5.0'})
				with urllib.request.urlopen(icon_req, timeout=5) as img_resp:
					with open(icon_path, "wb") as f:
						f.write(img_resp.read())
				if DEBUG_MODE:
					logger.info(f"[SERPAPI] Icône téléchargée pour {pkg}")
	except Exception as e:
		if DEBUG_MODE:
			logger.error(f"[SERPAPI] Erreur icône pour {pkg}: {e}")


def start_dbus_listener():
	try:
		bus = dbus.SessionBus()
		notif_obj = bus.get_object('org.freedesktop.Notifications', '/org/freedesktop/Notifications')
		notif_interface = dbus.Interface(notif_obj, 'org.freedesktop.Notifications')

		def handle_action_invoked(received_id, action_key):
			with active_notifications_lock:
				pkg = active_notifications.pop(received_id, None)
			if pkg:
				if DEBUG_MODE:
					logger.info(f"[INTERACTION] Clic sur ID {received_id} (Pkg: {pkg})")

				if OPEN_APP_ON_CLICK:
					subprocess.run(
						["adb", "shell", "input", "keyevent", "KEYCODE_WAKEUP"],
						stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
					)
					subprocess.run(
						["adb", "shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"],
						stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
					)

		def handle_notification_closed(received_id, reason):
			with active_notifications_lock:
				active_notifications.pop(received_id, None)

		notif_interface.connect_to_signal("ActionInvoked", handle_action_invoked)
		notif_interface.connect_to_signal("NotificationClosed", handle_notification_closed)
		loop = GLib.MainLoop()
		loop.run()
	except Exception as e:
		if DEBUG_MODE:
			logger.error(f"[DBUS-LISTENER] Erreur D-Bus : {e}", exc_info=True)

APP_NAMES_FR = {
    "com.google.android.apps.messaging": "Messages",
    "com.whatsapp": "WhatsApp",
    "com.instagram.android": "Instagram",
    "com.discord": "Discord",
    "com.google.android.gm": "Gmail",
    "org.telegram.messenger": "Telegram",
    "com.facebook.orca": "Messenger",
    "com.snapchat.android": "Snapchat",
    "com.uptodown.lite": "Uptodown",
    "com.uptodown": "Uptodown"
}

def _has_connected_adb_device():
	result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=3)
	lines = result.stdout.strip().splitlines()[1:]
	for line in lines:
		parts = line.split()
		if len(parts) >= 2 and parts[-1] == "device":
			return True
	return False


def connect_with_retry():
	"""Vérifie si un appareil est détecté par ADB (USB ou TCP/IP)."""
	while True:
		try:
			if _has_connected_adb_device():
				logger.info("Appareil Android détecté via ADB.")
				return True

			if AUTO_RECONNECT and ADB_ADDRESS:
				subprocess.run(
					["adb", "connect", f"{ADB_ADDRESS}:{ADB_PORT}"],
					capture_output=True, text=True, timeout=3
				)
				if _has_connected_adb_device():
					logger.info(f"Appareil Android connecté via ADB ({ADB_ADDRESS}:{ADB_PORT}).")
					return True

			if DEBUG_MODE:
				logger.warning("Aucun appareil détecté. Nouvelle tentative dans 5 secondes...")
		except Exception:
			pass
		time.sleep(5)


def notification_key(n):
	return (
		n.get("pkg") or "",
		n.get("title") or "",
		n.get("text") or "",
		n.get("time", -1),
	)


def main():
	parse_config()

	if not os.path.exists(ICONS_DIR):
		os.makedirs(ICONS_DIR)

	dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
	logger.info("Démarrage du script - Envoi des notifications...")
	threading.Thread(target=start_dbus_listener, daemon=True).start()

	visited = set()
	initial_baseline_pending = ONLY_NEW_NOTIFICATIONS

	while True:
		try:
			connect_with_retry()

			proc = subprocess.run(
				["adb", "shell", "dumpsys notification --noredact"],
				capture_output=True, text=True, timeout=10
			)
			if proc.returncode != 0:
				raise RuntimeError(proc.stderr.strip() or f"adb exited with code {proc.returncode}")

			notifications = parse_notifications(proc.stdout)
			current_keys = {notification_key(n) for n in notifications}

			if initial_baseline_pending:
				visited.update(current_keys)
				initial_baseline_pending = False
				if DEBUG_MODE:
					logger.info(f"[INIT] {len(current_keys)} notification(s) existante(s) ignorée(s) au démarrage.")
				notifications = []
			else:
				if packages:
					notifications = filter_package_names(notifications)

				notifications = filter_duplicate_notifications(notifications, visited)
				notifications = filter_unwanted_notifications(notifications)

			for n in notifications:
				pkg = n["pkg"] or "unknown"
				title = n.get("title") or ""
				text = n.get("text") or ""

				app_display_name = APP_NAMES_FR.get(pkg, pkg.split('.')[-1].capitalize())

				icon_path = os.path.abspath(os.path.join(ICONS_DIR, f"{pkg}.png"))
				if FETCH_ICONS and not os.path.exists(icon_path):
					fetch_icon_via_serpapi(pkg, icon_path)
					if not os.path.exists(icon_path):
						icon_path = ""
				elif not FETCH_ICONS or not os.path.exists(icon_path):
					icon_path = ""

				send_interactive_notification(title, text, app_display_name, icon_path, pkg)

			if len(visited) > MAX_VISITED_NOTIFICATIONS:
				visited = current_keys

		except Exception as e:
			if DEBUG_MODE:
				logger.error(f"Erreur dans la boucle : {e}")
			logger.warning("Connexion perdue avec l'appareil. En attente...")

		time.sleep(interval)


def send_interactive_notification(title, message, app_name, icon_path, pkg):
	try:
		bus = dbus.SessionBus()
		notif_obj = bus.get_object('org.freedesktop.Notifications', '/org/freedesktop/Notifications')
		notif_interface = dbus.Interface(notif_obj, 'org.freedesktop.Notifications')

		actions = ["default", "Ouvrir"] if (OPEN_APP_ON_CLICK and pkg != "unknown") else []
		hints = {}
		if icon_path and os.path.exists(icon_path):
			hints["image-path"] = icon_path

		notif_id = notif_interface.Notify(
			app_name,
			0,
			icon_path if icon_path else "",
			title,
			message,
			actions,
			hints,
			-1
		)

		if OPEN_APP_ON_CLICK and pkg != "unknown":
			with active_notifications_lock:
				if len(active_notifications) >= MAX_ACTIVE_NOTIFICATIONS:
					oldest_id = next(iter(active_notifications))
					del active_notifications[oldest_id]
				active_notifications[notif_id] = pkg

	except Exception as e:
		if DEBUG_MODE:
			logger.error(f"[DBUS-NOTIF] Erreur d'envoi : {e}")


def filter_unwanted_notifications(notifications):
	filtered = []
	for n in notifications:
		title = (n.get("title") or "").lower()
		text = (n.get("text") or "").lower()

		should_ignore = any(kw in title or kw in text for kw in SKIP_KEYWORDS)
		if should_ignore:
			if DEBUG_MODE:
				logger.info(f"[FILTRE] Ignoré -> Titre: '{n.get('title')}' | Texte: '{n.get('text')}'")
		else:
			filtered.append(n)
	return filtered


def filter_duplicate_notifications(notifications, visited):
	ret = []
	for n in notifications:
		key = notification_key(n)
		if key not in visited:
			visited.add(key)
			ret.append(n)
	return ret


def filter_package_names(notifications):
	return [n for n in notifications if n.get("pkg") in packages]


def parse_config():
	global packages, interval, privkey, pubkey, SERPAPI_KEY, FETCH_ICONS
	global OPEN_APP_ON_CLICK, SKIP_KEYWORDS, ONLY_NEW_NOTIFICATIONS
	global AUTO_RECONNECT, ADB_ADDRESS, ADB_PORT

	config = configparser.ConfigParser()
	config_path = os.path.join(BASE_DIR, "config.ini")
	config.read(config_path, encoding="utf-8")

	if not config.has_section('config'):
		return

	section = config['config']
	if "adbprivkey" in section and section["adbprivkey"].strip():
		privkey = section["adbprivkey"].strip()
	if "adbpubkey" in section and section["adbpubkey"].strip():
		pubkey = section["adbpubkey"].strip()

	if "address" in section:
		ADB_ADDRESS = section["address"].strip()
	if config.has_option('config', 'port'):
		ADB_PORT = config.getint('config', 'port', fallback=5555)
	if config.has_option('config', 'auto_reconnect'):
		AUTO_RECONNECT = config.getboolean('config', 'auto_reconnect', fallback=True)

	if "serpapi_key" in section:
		SERPAPI_KEY = section["serpapi_key"].strip()
	if config.has_option('config', 'fetch_icons'):
		FETCH_ICONS = config.getboolean('config', 'fetch_icons', fallback=True)
	if config.has_option('config', 'open_app_on_click'):
		OPEN_APP_ON_CLICK = config.getboolean('config', 'open_app_on_click', fallback=True)
	if config.has_option('config', 'only_new_notifications'):
		ONLY_NEW_NOTIFICATIONS = config.getboolean('config', 'only_new_notifications', fallback=True)
	if config.has_option('config', 'skip_keywords'):
		raw_skips = config.get('config', 'skip_keywords')
		SKIP_KEYWORDS = [kw.strip().lower() for kw in raw_skips.split(",") if kw.strip()]

	if "packages" in section:
		pkgs = section["packages"].split(",")
		packages = {x.strip() for x in pkgs if x.strip()}
	else:
		packages = set()

	interval = section.getint("interval", fallback=10)

if __name__ == "__main__":
	main()
