"""This PC's devices (devices.json) and the PLUX hub it uses. No hardware or GUI needed here.

devices.json holds {"plux": bool, "flow": bool, "hub": "00:07:80:..." or null}. Files written
before the hub was stored have no "hub" key: that means "not set", and the app looks for the hub.
"""

import json
import re
from pathlib import Path

DEVICES_FILE = Path(__file__).resolve().parent / "devices.json"
DEFAULTS = {"plux": True, "flow": True, "hub": None}
HUB_NAME = "biosignalsplux"  # PLUX's device description for the hub

_MAC = re.compile(r"[0-9A-F]{2}([:-])[0-9A-F]{2}(\1[0-9A-F]{2}){4}")


def normalize_mac(text):
    """'00:07:80:4b:3f:25' or '00-07-80-4B-3F-25' -> '00:07:80:4B:3F:25'; None if not six hex pairs."""
    if not isinstance(text, str):
        return None
    text = text.strip().upper()
    if not _MAC.fullmatch(text):
        return None
    return text.replace("-", ":")


def load_devices(path=DEVICES_FILE):
    """This PC's devices; defaults for anything missing or unreadable (a bad hub MAC = not set)."""
    devices = dict(DEFAULTS)
    try:
        saved = json.loads(Path(path).read_text())
        if isinstance(saved, dict):
            devices.update(saved)
    except (OSError, ValueError):
        pass
    devices["hub"] = normalize_mac(devices["hub"])
    return devices


def save_devices(changes, path=DEVICES_FILE):
    """Update some settings in devices.json, keeping the others. Returns the saved settings."""
    devices = load_devices(path)
    devices.update(changes)
    Path(path).write_text(json.dumps(devices))
    return devices


def find_hubs(found):
    """[(MAC, description)] of the biosignalsplux hubs among plux.BaseDev.findDevices() results.

    Each result holds a path such as 'BTH00:07:80:4B:3F:25' and a description; accepted as a
    (path, description) pair or a {"path", "description"} dict.
    """
    hubs = []
    for dev in found or ():
        if isinstance(dev, dict):
            path, desc = dev.get("path", ""), dev.get("description", "")
        else:
            path, desc = (list(dev) + ["", ""])[:2]
        path, desc = str(path), str(desc)
        mac = normalize_mac(path[3:]) if path.upper().startswith("BTH") else None
        if mac and HUB_NAME in desc.lower() and mac not in [m for m, _ in hubs]:
            hubs.append((mac, desc))
    return hubs


def parse_hub_lines(text):
    """[(MAC, description)] from `plux_lsl.py --find` output: one 'MAC  description' line per hub."""
    hubs = []
    for line in text.splitlines():
        parts = line.split(None, 1)
        mac = normalize_mac(parts[0]) if parts else None
        if mac:
            hubs.append((mac, parts[1].strip() if len(parts) > 1 else ""))
    return hubs


def auto_hub(hubs):
    """The hub to use without asking: the only one found, else None (none, or the RA picks)."""
    return hubs[0][0] if len(hubs) == 1 else None
