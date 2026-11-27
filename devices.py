"""This PC's devices (devices.json) and the PLUX hub it uses. No hardware or GUI needed here.

devices.json holds {"plux": bool, "flow": bool, "hub": "00:07:80:..." or null,
"ports": {"1": "RESP", "2": "ECG"}}. Files written before the hub was stored have no "hub" key:
that means "not set", and the app looks for the hub. "ports" says which sensor is on which hub
port for sensors without an ID chip; missing means the lab's standard wiring (DEFAULT_PORTS).
"""

import json
import re
from pathlib import Path

DEVICES_FILE = Path(__file__).resolve().parent / "devices.json"
HUB_NAME = "biosignalsplux"  # PLUX's device description for the hub
# PLUX sensor class (from a sensor's ID chip) -> kind; the kind is the channel label and type
SENSOR_CLASS = {
    0: "UNKNOWN", 1: "EMG", 2: "ECG", 3: "LIGHT", 4: "EDA", 5: "BVP",
    6: "RESP", 7: "XYZ", 8: "SYNC", 9: "EEG", 10: "SYNC_ADAP", 11: "SYNC_LED",
    12: "SYNC_SW", 13: "USB", 14: "FORCE", 15: "TEMP", 16: "VPROBE",
    17: "BREAKOUT", 18: "OXIMETER", 19: "GONI", 20: "ACT", 21: "EOG",
    22: "EGG", 23: "ANSA", 26: "OSL",
}
DEFAULT_PORTS = {1: "RESP", 2: "ECG"}  # standard wiring: breathing belt on port 1, ECG on port 2
KIND_WORDS = {"RESP": "breathing belt (RESP)", "ECG": "ECG"}
DEFAULTS = {"plux": True, "flow": True, "hub": None, "ports": DEFAULT_PORTS}

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
    devices["ports"] = normalize_ports(devices["ports"]) or dict(DEFAULT_PORTS)
    return devices


def save_devices(changes, path=DEVICES_FILE):
    """Update some settings in devices.json, keeping the others. Returns the saved settings."""
    devices = load_devices(path)
    devices.update(changes)
    Path(path).write_text(json.dumps(devices))
    return devices


def normalize_ports(ports):
    """{"1": "resp", "2": "ECG"} -> {1: "RESP", 2: "ECG"}; entries that are not a port 1-11 with a
    known sensor kind are dropped (None if nothing is left)."""
    if not isinstance(ports, dict):
        return None
    kinds = set(SENSOR_CLASS.values()) - {"UNKNOWN"}
    out = {}
    for port, kind in ports.items():
        try:
            port = int(port)
        except (TypeError, ValueError):
            continue
        kind = kind.strip().upper() if isinstance(kind, str) else None
        if 1 <= port <= 11 and kind in kinds:
            out[port] = kind
    return dict(sorted(out.items())) or None


def parse_port_kinds(text):
    """'1=RESP,2=ECG' (plux_lsl.py --port-kinds) -> {1: "RESP", 2: "ECG"}; ValueError if malformed."""
    pairs = [item.split("=") for item in text.split(",") if item.strip()]
    if any(len(pair) != 2 for pair in pairs):
        raise ValueError(f"expected PORT=KIND pairs such as 1=RESP,2=ECG, got {text!r}")
    ports = normalize_ports(dict(pairs)) or {}
    if len(ports) != len(pairs):
        raise ValueError(f"unknown port or sensor kind in {text!r} (ports 1-11, kinds such as RESP, ECG)")
    return ports


def format_port_kinds(ports):
    """{1: "RESP", 2: "ECG"} -> '1=RESP,2=ECG'."""
    return ",".join(f"{port}={kind}" for port, kind in sorted(ports.items()))


def describe_ports(ports):
    """{1: "RESP", 2: "ECG"} -> 'breathing belt (RESP) on port 1, ECG on port 2' (for the RA)."""
    return ", ".join(f"{KIND_WORDS.get(kind, kind)} on port {port}" for port, kind in sorted(ports.items()))


def plan_ports(chips, port_kinds, forced=None):
    """Which hub ports to record, and as which kind of sensor.

    chips: {port: kind} from the sensors' ID chips (hub.getSensors()); "UNKNOWN" = no chip info.
    port_kinds: this PC's port map, for sensors without an ID chip. forced: --ports, or None.
    Returns ([(port, kind)], assumed) where assumed is True when no sensor was detected at all
    and the port map alone decided which ports to record.
      - a sensor with an ID chip keeps the chip's kind
      - a port without chip info takes the kind from the port map (else UNKNOWN)
      - no sensors detected (and no --ports): the ports in the port map
    """
    if forced:
        ports, assumed = list(forced), False
    elif chips:
        ports, assumed = sorted(chips), False
    else:
        ports, assumed = sorted(port_kinds), True
    plan = []
    for port in ports:
        kind = chips.get(port, "UNKNOWN")
        if kind == "UNKNOWN":
            kind = port_kinds.get(port, "UNKNOWN")
        plan.append((port, kind))
    return plan, assumed


def find_hubs(found):
    """[(MAC, description)] of the biosignalsplux hubs among plux.BaseDev.findDevices() results.

    API 1.10 returns a tuple of (path, description) pairs, e.g. ('BTH00:07:80:4D:33:82',
    'biosignalsplux'); a {"path", "description"} dict is accepted too.
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


_PNP_DEV = re.compile(r"BTHENUM\\DEV_([0-9A-F]{12})\\", re.IGNORECASE)


def paired_hubs(text):
    """([(MAC, name)] present, [(MAC, name)] not present) for the biosignalsplux hubs paired in
    Windows, from Get-PnpDevice lines 'InstanceId|Present|FriendlyName', e.g.
    'BTHENUM\\DEV_0007804D3382\\...|True|biosignalsplux'. Not present: removed, or Bluetooth off."""
    present, absent = [], []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3 or HUB_NAME not in parts[2].lower():
            continue
        dev = _PNP_DEV.match(parts[0])
        if not dev:
            continue
        mac = ":".join(dev.group(1).upper()[i:i + 2] for i in range(0, 12, 2))
        group = present if parts[1].lower() == "true" else absent
        if mac not in [m for m, _ in present + absent]:
            group.append((mac, parts[2]))
    return present, absent


def candidate_hubs(present, absent, found=()):
    """The hubs to offer: those paired and present in Windows; if there are none, old pairings
    plus the hubs PLUX's findDevices() sees (it can miss a paired hub, and may list other
    people's hubs in range, so it is only the fallback)."""
    if present:
        return list(present)
    hubs = []
    for mac, desc in list(absent) + find_hubs(found):
        if mac not in [m for m, _ in hubs]:
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
