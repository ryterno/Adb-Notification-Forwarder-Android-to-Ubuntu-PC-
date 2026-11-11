# Adb Notification Forwarder (Android to Ubuntu/PC)

A simple application that reads notifications from Android devices or emulators using the `adb` interface and displays them interactively on your PC desktop using D-Bus pop-ups.

## Features

- **Real-time Forwarding**: Reads notifications via `dumpsys notification` over TCP/IP ADB.
- **Interactive Triggers**: Clicking a notification on your desktop automatically wakes up your Android screen and opens the corresponding app.
- **Dynamic Icons**: Automatically fetches and caches app icons from the Google Play Store using SerpApi.
- **Smart Filtering**: Configurable keyword filtering to skip unwanted system notifications (like charging, USB status, or custom keywords).
- **Robust Auto-Reconnect**: Automatically recovers the ADB connection if dropped (`adb tcpip 5555` / `adb forward`).
- **Debug Mode**: Toggle verbose logging using the `-debug` command-line argument.

---

## Install & Run

### 1. Install dependencies

```
pip3 install -r requirements.txt
```

### Run

```
python3 server.py
```

## Config

Config the application via `config.ini`. 


## License Information

The project is released under the MIT license. See LICENSE.txt for the license text.

The project uses the following libraries and includes compiled binaries of them in the released, prebuilt binaries:

- [Plyer](https://github.com/kivy/plyer) fron kivy. plyer is released under the MIT license. The original license text is reproduced in `NOTICE.txt`

- [adb_shell](https://github.com/JeffLIrion/adb_shell) from Jeff Irion. adb_shell is released under Apache 2.0 license. The original license text is reproduced in `NOTICE.txt`
