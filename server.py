import time
import os
import sys
import logging
import configparser
import subprocess
import threading
from datetime import datetime
import dbus
import dbus.mainloop.glib
from gi.repository import GLib
from adb_shell.adb_device import AdbDeviceTcp, AdbDeviceUsb
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
import urllib.request
import json
import re

# Gestion du mode debug via l'argument -debug
DEBUG_MODE = "-debug" in sys.argv

# Configuration des logs selon le mode debug
log_level = logging.INFO if DEBUG_MODE else logging.WARNING
logging.basicConfig(level=log_level, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('main')

# Dictionnaire global pour lier l'ID de notification au package de l'application
active_notifications = {}

# Paramètres globaux modifiables via config.ini
packages = set()
interval = 10
address = ""
port = 0
privkey = os.path.join(os.path.expanduser("~"), ".android", "adbkey")
pubkey = privkey + ".pub"
ICONS_DIR = "icons"

SERPAPI_KEY = ""
FETCH_ICONS = True
AUTO_RECONNECT = True
OPEN_APP_ON_CLICK = True
SKIP_KEYWORDS = ["charge", "chargement", "usb"]

# Expressions régulières pour le parsing des notifications
exp_start = re.compile(r"\s*NotificationRecord.*")
exp_title = re.compile(r"\s*android\.title=String \((.*)\)")
exp_text = re.compile(r"\s*android\.text=String \((.*)\)")
exp_time = re.compile(r"\s*mUpdateTimeMs=([0-9]*)")
exp_stop = re.compile(r"(\s*NotificationRecord)|(^\s*$)")
exp_pkg = re.compile(r"pkg=(.*)")

def parse_notifications(result):
	ret = []
	lines = result.split("\n")
	entry = False
	current = {}
	i = -1
	while True:
		i += 1
		if i >= len(lines):
			break
		line = lines[i]
		if entry:
			match_title = exp_title.match(line)
			match_text = exp_text.match(line)
			match_time = exp_time.match(line)
			terminate = exp_stop.match(line)         
			if terminate:
				i -= 1
				entry = False
				if not "time" in current:
					current["time"] = -1
				if "title" in current or "text" in current:
					if "title" not in current:
						current["title"] = current.get("pkg", "Notification")
					if "text" not in current:
						current["text"] = ""
					ret.append(current)
				current = {}
				continue

			if match_title:
				current["title"] = match_title.group(1)
			if match_text:
				current["text"] = match_text.group(1)
			if match_time:
				current["time"] = int(match_time.group(1))
				
		else:
			if exp_start.match(line):
				entry = True
				s = line.strip().split(" ")
				if len(s) >= 2:
					pkg = s[1]
					match_pkg = exp_pkg.match(pkg)
					if match_pkg:
						current["pkg"] = match_pkg.group(1)
					else:
						current["pkg"] = None
	return ret

def fetch_icon_via_serpapi(pkg, icon_path):
	if not FETCH_ICONS or not SERPAPI_KEY:
		return
	try:
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
				urllib.request.urlretrieve(icon_url, icon_path)
	except Exception as e:
		if DEBUG_MODE:
			logger.error(f"[SERPAPI] Erreur icône pour {pkg}: {e}")

def start_dbus_listener():
	dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
	try:
		bus = dbus.SessionBus()
		notif_obj = bus.get_object('org.freedesktop.Notifications', '/org/freedesktop/Notifications')
		notif_interface = dbus.Interface(notif_obj, 'org.freedesktop.Notifications')
		
		def handle_action_invoked(received_id, action_key):
			if received_id in active_notifications:
				pkg = active_notifications[received_id]
				if DEBUG_MODE:
					logger.info(f"[INTERACTION] Clic sur ID {received_id} (Pkg: {pkg})")
				
				if OPEN_APP_ON_CLICK:
					subprocess.run(["adb", "shell", "input", "keyevent", "KEYCODE_WAKEUP"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
					subprocess.run(["adb", "shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
				
				del active_notifications[received_id]

		notif_interface.connect_to_signal("ActionInvoked", handle_action_invoked)
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

def connect_with_retry():
	"""Tente de se connecter en boucle toutes les 5 secondes si aucun téléphone n'est trouvé."""
	while True:
		try:
			if AUTO_RECONNECT:
				subprocess.run(["adb", "tcpip", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
				subprocess.run(["adb", "forward", f"tcp:{port}", f"tcp:{port}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
			
			with open(privkey) as f:
				priv = f.read()
			with open(pubkey) as f:
				pub = f.read()

			signer = PythonRSASigner(pub, priv)
			device = AdbDeviceTcp(address, port, default_transport_timeout_s=5.)
			device.connect(rsa_keys=[signer], auth_timeout_s=2.0)
			logger.info("Appareil Android détecté et connecté avec succès.")
			return device
		except Exception:
			logger.warning(f"Aucun appareil détecté sur {address}:{port}. Nouvelle tentative dans 5 secondes...")
			time.sleep(5)

def main():
	parse_config()

	if not os.path.exists(ICONS_DIR):
		os.makedirs(ICONS_DIR)

	logger.info("Démarrage du script - Envoi des notifications...")
	threading.Thread(target=start_dbus_listener, daemon=True).start()

	device = None
	visited = set()
	
	while True:
		try:
			if not device:
				device = connect_with_retry()

			raw = device.shell("dumpsys notification --noredact")
			notifications = parse_notifications(raw)
			
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
				elif not FETCH_ICONS:
					icon_path = ""

				send_interactive_notification(title, text, app_display_name, icon_path, pkg)

		except Exception as e:
			logger.warning("Connexion perdue avec l'appareil Android. Passage en mode attente...")
			device = None  # Force la réinitialisation de la connexion

		time.sleep(interval)
		if len(visited) > 2*1000:
			visited = set()

def send_interactive_notification(title, message, app_name, icon_path, pkg):
	try:
		bus = dbus.SessionBus()
		notif_obj = bus.get_object('org.freedesktop.Notifications', '/org/freedesktop/Notifications')
		notif_interface = dbus.Interface(notif_obj, 'org.freedesktop.Notifications')
		
		actions = ["default", "Ouvrir"] if OPEN_APP_ON_CLICK else []
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

		if OPEN_APP_ON_CLICK:
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
		if not should_ignore:
			filtered.append(n)
	return filtered

def filter_duplicate_notifications(notifications, visited):
	ret = []
	for n in notifications:
		if not n["time"] in visited:
			visited.add(n["time"])
			ret.append(n)
	return ret

def filter_package_names(notifications):
	result = filter(lambda n : n["pkg"] in packages, notifications)
	return list(result)

def parse_config():
	global packages, interval, address, port, privkey, pubkey, SERPAPI_KEY, FETCH_ICONS, AUTO_RECONNECT, OPEN_APP_ON_CLICK, SKIP_KEYWORDS

	config = configparser.ConfigParser()
	config.read("config.ini")
	section = config['config']
	if "adbprivkey" in section:
		privkey = section["adbprivkey"]
	if "adbpubkey" in section:
		pubkey = section["adbpubkey"]
	
	if "serpapi_key" in section:
		SERPAPI_KEY = section["serpapi_key"]
	if config.has_option('config', 'fetch_icons'):
		FETCH_ICONS = config.getboolean('config', 'fetch_icons')
	if config.has_option('config', 'auto_reconnect'):
		AUTO_RECONNECT = config.getboolean('config', 'auto_reconnect')
	if config.has_option('config', 'open_app_on_click'):
		OPEN_APP_ON_CLICK = config.getboolean('config', 'open_app_on_click')
	if config.has_option('config', 'skip_keywords'):
		raw_skips = config.get('config', 'skip_keywords')
		SKIP_KEYWORDS = [kw.strip().lower() for kw in raw_skips.split(",") if kw.strip()]

	if "packages" in section:
		pkgs = section["packages"].split(",")
		packages = set(map(lambda x: x.strip(), pkgs))
	else:
		packages = set()

	address = section["address"]
	port = section.getint("port")
	interval = section.getint("interval")

if __name__ == "__main__":
	main()
