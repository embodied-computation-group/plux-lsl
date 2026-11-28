"""Session Runner: walks the RA through one lab session, step by step, from a protocol file.

Start it from windows\\session_runner.bat [protocol], or:
    .venv\\Scripts\\pythonw.exe session_runner.py [--protocol full-battery] [--demo]
    .venv\\Scripts\\python.exe session_runner.py --check full-battery   (no window: list what is not set up)

Screens: start (RA initials, participant, session) -> device check -> one screen per protocol step
(instructions, tick boxes, Start opens the task, Done / Problem; a list of all steps on the left opens any of
them, so the protocol's order is only the default) -> end summary. It does not control the
Physio Recorder (that starts and stops recording by itself) and never passes anything to the tasks: it
shows what to type. Session log (text + JSON) per session in the protocol's log_dir.
"""

import argparse
import ctypes
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROTOCOL_DIR = ROOT / "protocols"
DEFAULT_LOG_DIR = "logs/session_runner"
PLACEHOLDER = "FILL IN"   # a launch value containing this has not been set up on this PC yet
TOKENS = ("participant", "session", "initials")   # {participant} etc. in type_into_task values
DEMO_LAUNCH = {"command": ["notepad.exe"]}

# name -> (shown name, what to do when it is red, USB (VID, PID) or None for the Physio Recorder window)
DEVICES = {
    "physio_recorder": ("Physio Recorder is open", "Open Physio Recorder from the desktop.", None),
    "rrst_teensy": ("RRST device (USB)", "Plug the RRST device's USB cable into this laptop.", (0x16C0, 0x0483)),
    "sensorbridge": ("Flow sensor SensorBridge (USB)", "Plug the SensorBridge's USB cable into this laptop.",
                     (0x0403, 0x7168)),
}
PHYSIO_TITLE = "Physio Recorder"   # physio_app.py's window title

NOT_STARTED, STARTED, DONE, PROBLEM, SKIPPED = "not started", "started", "done", "problem", "skipped"
FINISHED = (DONE, PROBLEM, SKIPPED)   # the RA has dealt with the step (Next step is allowed)
STEP_KEYS = {"id", "title", "instructions", "prompts", "launch", "type_into_task"}
TOP_KEYS = {"name", "order_status", "log_dir", "devices", "participant_digits_only", "steps"}


class ProtocolError(Exception):
    """A protocol file that cannot be used; the message is shown to the RA as it is."""


# ---------------------------------------------------------------- protocol file

def protocol_names(folder=PROTOCOL_DIR):
    """The protocol files in protocols/, by name (full-battery, rrst-only, ...)."""
    return sorted(p.stem for p in Path(folder).glob("*.json"))


def protocol_path(name_or_path, folder=PROTOCOL_DIR):
    """A protocol name (rrst-only) or a path to a .json file."""
    text = str(name_or_path).strip()
    if text.lower().endswith(".json") or "/" in text or "\\" in text:
        return Path(text)
    return Path(folder) / f"{text}.json"


def safe_log_dir(value, root=ROOT):
    """The log folder (relative to the plux-lsl folder unless absolute); never inside a data folder."""
    path = Path(os.path.expandvars(value))
    if not path.is_absolute():
        path = Path(root) / path
    path = path.resolve()
    if any(part.lower() == "data" for part in path.parts):
        raise ProtocolError(f"log_dir {value!r} is inside a data folder; session logs never go into data.")
    return path


def _strip_notes(value):
    """Keys starting with "_" are notes for whoever edits the file; they are ignored."""
    if isinstance(value, dict):
        return {k: _strip_notes(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, list):
        return [_strip_notes(v) for v in value]
    return value


def _check_launch(launch, where, problems):
    if launch is None:
        return
    if not isinstance(launch, dict) or set(launch) - {"open", "command", "cwd"}:
        problems.append(f"{where}: launch must be null, {{\"open\": path}} or {{\"command\": [...], \"cwd\": ...}}")
        return
    if ("open" in launch) == ("command" in launch):
        problems.append(f"{where}: launch needs exactly one of \"open\" or \"command\"")
    if "open" in launch and not (isinstance(launch["open"], str) and launch["open"].strip()):
        problems.append(f"{where}: launch \"open\" must be a path")
    if "command" in launch:
        cmd = launch["command"]
        if not (isinstance(cmd, list) and cmd and all(isinstance(c, str) and c for c in cmd)):
            problems.append(f"{where}: launch \"command\" must be a list of strings, e.g. [\"C:\\\\...\\\\python.exe\", "
                            "\"task.py\"]")
    if "cwd" in launch and not isinstance(launch["cwd"], str):
        problems.append(f"{where}: launch \"cwd\" must be a folder path")


def validate_protocol(raw, root=ROOT):
    """The protocol as a clean dict, or ProtocolError listing every problem."""
    if not isinstance(raw, dict):
        raise ProtocolError("The protocol file must contain one JSON object { ... }.")
    raw = _strip_notes(raw)
    problems = [f"unknown setting {k!r}" for k in sorted(set(raw) - TOP_KEYS)]
    name = raw.get("name")
    if not (isinstance(name, str) and name.strip()):
        problems.append("\"name\" missing (e.g. \"Full battery\")")
    digits_only = raw.get("participant_digits_only", False)
    if not isinstance(digits_only, bool):
        problems.append("\"participant_digits_only\" must be true or false")
    devices = raw.get("devices", [])
    if not isinstance(devices, list) or any(d not in DEVICES for d in devices):
        problems.append(f"\"devices\" must be a list of: {', '.join(DEVICES)}")
        devices = []
    steps = raw.get("steps")
    if not (isinstance(steps, list) and steps):
        problems.append("\"steps\" missing or empty")
        steps = []
    seen = set()
    clean_steps = []
    for n, step in enumerate(steps, 1):
        where = f"step {n}"
        if not isinstance(step, dict):
            problems.append(f"{where}: must be an object {{ ... }}")
            continue
        sid = step.get("id")
        if isinstance(sid, str) and re.fullmatch(r"[a-z0-9_-]+", sid):
            where = f"step {n} ({sid})"
            if sid in seen:
                problems.append(f"{where}: id used twice")
            seen.add(sid)
        else:
            problems.append(f"{where}: \"id\" must be lower-case letters, digits, - or _ (e.g. \"hrd\")")
        problems += [f"{where}: unknown setting {k!r}" for k in sorted(set(step) - STEP_KEYS)]
        for key in ("title", "instructions"):
            if not (isinstance(step.get(key), str) and step[key].strip()):
                problems.append(f"{where}: \"{key}\" missing")
        prompts = step.get("prompts", [])
        if not (isinstance(prompts, list) and all(isinstance(p, str) and p.strip() for p in prompts)):
            problems.append(f"{where}: \"prompts\" must be a list of texts")
            prompts = []
        if "launch" not in step:
            problems.append(f"{where}: \"launch\" missing (null if nothing is opened)")
        _check_launch(step.get("launch"), where, problems)
        typed = step.get("type_into_task", [])
        if not (isinstance(typed, list) and all(isinstance(t, list) and len(t) == 2 and all(isinstance(x, str)
                                                                                         for x in t) for t in typed)):
            problems.append(f"{where}: \"type_into_task\" must be a list of [\"what the task asks\", \"value\"] pairs")
            typed = []
        for _, value in typed:
            for token in re.findall(r"\{(\w*)\}", value):
                if token not in TOKENS:
                    problems.append(f"{where}: unknown {{{token}}} in type_into_task (use "
                                    + ", ".join("{" + t + "}" for t in TOKENS) + ")")
        clean_steps.append({"id": sid, "title": step.get("title"), "instructions": step.get("instructions"),
                            "prompts": prompts, "launch": step.get("launch"), "type_into_task": typed})
    try:
        log_dir = safe_log_dir(raw.get("log_dir") or DEFAULT_LOG_DIR, root)
    except ProtocolError as e:
        problems.append(str(e))
        log_dir = None
    if problems:
        raise ProtocolError("The protocol file has problems:\n- " + "\n- ".join(problems))
    return {"name": name.strip(), "order_status": raw.get("order_status", ""), "log_dir": log_dir,
            "devices": devices, "participant_digits_only": digits_only, "steps": clean_steps}


def load_protocol(name_or_path, folder=PROTOCOL_DIR, root=ROOT, demo=False):
    """Read and check a protocol file. demo: every launch opens Notepad instead."""
    path = protocol_path(name_or_path, folder)
    if not path.is_file():
        names = ", ".join(protocol_names(folder)) or "none"
        raise ProtocolError(f"Protocol file not found: {path}\nAvailable in {folder}: {names}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise ProtocolError(f"Could not read the protocol file {path}: {e}")
    except json.JSONDecodeError as e:
        raise ProtocolError(f"The protocol file {path} is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}")
    protocol = validate_protocol(raw, root)
    protocol["file"] = str(path)
    if demo:
        for step in protocol["steps"]:
            if step["launch"] is not None:
                step["launch"] = dict(DEMO_LAUNCH)
    return protocol


def fill(template, who):
    """{participant}, {session}, {initials} replaced by what the RA entered."""
    for token in TOKENS:
        template = template.replace("{" + token + "}", who[token])
    return template


# ---------------------------------------------------------------- start screen input

def validate_start(initials, participant, session, digits_only=False):
    """(values, problems): the start screen's three fields, cleaned, and what is wrong with them.
    digits_only: the protocol's participant_digits_only. Then the participant must be exactly 4 digits
    (0001-9999): RRST names its files RRST_%04d, so "1" and "0001" would not match the other tasks' files."""
    initials, participant, session = (str(x or "").strip() for x in (initials, participant, session))
    problems = []
    if not re.fullmatch(r"[A-Za-z]{2,4}", initials):
        problems.append("RA initials: 2 to 4 letters, e.g. JD")
    if digits_only and not (re.fullmatch(r"[0-9]{4}", participant) and participant != "0000"):
        problems.append("Participant: exactly 4 digits, e.g. 0007")
    elif not re.fullmatch(r"[A-Za-z0-9]{1,20}", participant):
        problems.append("Participant ID: letters and digits only (no spaces, dashes or underscores), e.g. 0123")
    if not re.fullmatch(r"[1-9][0-9]?", session):
        problems.append("Session: a number from 1 to 99 (type 1, not 01)")
    return {"initials": initials.upper(), "participant": participant, "session": session}, problems


# ---------------------------------------------------------------- launching

def desktop():
    """The current user's Desktop folder (where the laptops' task shortcuts are)."""
    return os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Desktop")


def resolve_open(value):
    """An "open" value: a bare file name ("HRD (Heart Rate Discrimination).lnk", or a web task's internet
    shortcut "Task.url") is looked up on the current user's Desktop; a path (with a folder) is used as it is.
    %VARS% are expanded."""
    value = os.path.expandvars(value)
    return value if os.path.dirname(value) else os.path.join(desktop(), value)


def launch_problem(launch):
    """None if the launch target can be started, else a message for the RA."""
    if launch is None:
        return None
    values = [launch.get("open", "")] + list(launch.get("command", [])) + [launch.get("cwd", "")]
    if any(PLACEHOLDER in v for v in values):
        return ("This step's launch target has not been set up on this laptop yet (protocol file says "
                f"'{PLACEHOLDER}'). Open the task from its desktop shortcut instead, and tell the researcher.")
    if "open" in launch:
        path = Path(resolve_open(launch["open"]))
        if not path.exists():
            return f"Cannot find {path}. Open the task from its desktop shortcut instead, and tell the researcher."
    else:
        exe = os.path.expandvars(launch["command"][0])
        if not (Path(exe).is_file() if os.path.dirname(exe) else shutil.which(exe)):
            return f"Cannot find the program {exe}. Open the task from its desktop shortcut instead, and tell the researcher."
    cwd = launch.get("cwd")
    if cwd and not Path(os.path.expandvars(cwd)).is_dir():
        return f"Cannot find the folder {cwd}. Tell the researcher."
    return None


def start_launch(launch):
    """Open the launch target without waiting for it. Raises OSError (message for the RA) on failure."""
    problem = launch_problem(launch)
    if problem:
        raise OSError(problem)
    if "open" in launch:
        os.startfile(resolve_open(launch["open"]))   # .lnk, .url, .bat, .exe: as a double-click would
        return
    cmd = [os.path.expandvars(c) for c in launch["command"]]
    cwd = os.path.expandvars(launch["cwd"]) if launch.get("cwd") else None
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)   # a console task gets its own window
    subprocess.Popen(cmd, cwd=cwd, creationflags=flags)


# ---------------------------------------------------------------- device check (read-only)

def window_titles():
    """Titles of all top-level windows (Windows only; empty elsewhere)."""
    if not hasattr(ctypes, "windll"):
        return []
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    titles = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            titles.append(buf.value)
        return True

    user32.EnumWindows(each, 0)
    return titles


def usb_ports():
    """(vid, pid) of every USB serial port present."""
    from serial.tools import list_ports
    return [(p.vid, p.pid) for p in list_ports.comports()]


def check_devices(names, titles=None, ports=None):
    """{name: (ok, detail)} for the protocol's devices. titles/ports: injected by tests."""
    results = {}
    title_error = port_error = None
    try:
        if titles is None and "physio_recorder" in names:
            titles = window_titles()
    except Exception as e:  # noqa: BLE001  (a failed check is shown red, never a crash)
        titles, title_error = None, e
    try:
        if ports is None and any(DEVICES[n][2] for n in names):
            ports = usb_ports()
    except Exception as e:  # noqa: BLE001
        ports, port_error = None, e
    for name in names:
        shown, hint, usb = DEVICES[name]
        if usb is None:
            if titles is None:
                results[name] = (False, f"could not check ({title_error}). {hint}")
            else:
                ok = PHYSIO_TITLE in titles
                results[name] = (ok, "window found" if ok else f"no Physio Recorder window. {hint}")
        else:
            if ports is None:
                results[name] = (False, f"could not check ({port_error}). {hint}")
            else:
                ok = tuple(usb) in ports
                results[name] = (ok, "found" if ok else f"not found ({usb[0]:04X}:{usb[1]:04X}). {hint}")
    return results


# ---------------------------------------------------------------- session state and log

def stamp(t):
    return t.strftime("%Y%m%d-%H%M%S")


def log_paths(log_dir, participant, session, t, demo=False):
    """(text, json) log paths that do not exist yet: session-runner_sub-P_ses-S_YYYYMMDD-HHMMSS[_demo]."""
    base = f"session-runner_sub-{participant}_ses-{session}_{stamp(t)}" + ("_demo" if demo else "")
    stem, n = base, 2
    while (Path(log_dir) / f"{stem}.txt").exists() or (Path(log_dir) / f"{stem}.json").exists():
        stem, n = f"{base}-{n}", n + 1
    return Path(log_dir) / f"{stem}.txt", Path(log_dir) / f"{stem}.json"


def clock():
    return datetime.datetime.now().replace(microsecond=0)


class Session:
    """What happened in one session. Every change is appended to the text log and the JSON is rewritten,
    so a crash loses nothing. A failing log write never stops the session: log_error says why."""

    def __init__(self, protocol, who, demo=False, now=clock):
        self.protocol, self.who, self.demo, self.now = protocol, dict(who), demo, now
        self.started = now()
        self.ended, self.end_reason = None, None
        self.devices = None
        self.events = []
        self.log_error = None
        self.steps = [{"id": s["id"], "title": s["title"], "status": NOT_STARTED, "attempts": [], "notes": []}
                      for s in protocol["steps"]]
        self.txt_path, self.json_path = log_paths(protocol["log_dir"], who["participant"], who["session"],
                                                  self.started, demo)
        self.event("SESSION", None, f"protocol {protocol['name']} ({Path(protocol.get('file', '?')).name}); "
                   f"RA {who['initials']}, "
                   f"participant {who['participant']}, session {who['session']}" + ("; DEMO MODE" if demo else ""))

    def step(self, step_id):
        return next(s for s in self.steps if s["id"] == step_id)

    def position(self, step_id):
        """0-based place of a step in the protocol's order."""
        return next(i for i, s in enumerate(self.steps) if s["id"] == step_id)

    def goto(self, from_id, to_id):
        """The RA opened a step from the step list instead of with Next / Back. Logged as GOTO unless it is
        what Back (the previous step) or Next step (the next one, once this step is finished) would have
        done. Returns True when logged."""
        i, j = self.position(from_id), self.position(to_id)
        if j == i or j == i - 1 or (j == i + 1 and self.step(from_id)["status"] in FINISHED):
            return False
        self.event("GOTO", to_id, f"opened out of order: from step {i + 1} ({from_id}, "
                                  f"{self.step(from_id)['status']}) to step {j + 1} ({to_id})")
        return True

    def open_steps(self):
        """(id, status) of the steps not finished yet: never started, or started without Done / Problem / Skip."""
        return [(s["id"], s["status"]) for s in self.steps if s["status"] not in FINISHED]

    def run_order(self):
        """Step ids in the order they were first started (the protocol's order is only the default)."""
        return [e["step"] for e in self.events if e["event"] == "START"]

    def completion(self):
        """The end reason for the end screen: says which steps are not finished, whatever order they ran in."""
        missing = self.open_steps()
        if not missing:
            return "completed: all steps shown"
        return "end screen with steps not finished: " + ", ".join(f"{sid} ({status})" for sid, status in missing)

    def event(self, kind, step_id, text):
        t = self.now()
        self.events.append({"time": t.isoformat(), "event": kind, "step": step_id, "text": text})
        line = f"{t:%Y-%m-%d %H:%M:%S}  {kind:<8} " + (f"[{step_id}] " if step_id else "") + text
        self._write(line)

    def _write(self, line):
        try:
            self.txt_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.txt_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            tmp = self.json_path.with_name(self.json_path.name + ".tmp")
            tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
            os.replace(tmp, self.json_path)
            self.log_error = None
        except OSError as e:
            self.log_error = f"SESSION LOG NOT SAVED ({e}). Write things down on paper and tell the researcher."

    def record_devices(self, results, skip_reason=None):
        all_ok = all(ok for ok, _ in results.values())
        self.devices = {"results": {k: {"ok": ok, "detail": d} for k, (ok, d) in results.items()},
                        "all_ok": all_ok, "skip_reason": skip_reason}
        text = ", ".join(f"{k} {'OK' if ok else 'MISSING'}" for k, (ok, _) in results.items()) or "none required"
        if skip_reason:
            self.event("DEVICES", None, f"{text}; CONTINUED WITHOUT ALL GREEN: {skip_reason}")
        else:
            self.event("DEVICES", None, text)

    def start(self, step_id, by_hand=False):
        """by_hand: the launch failed and the RA opened the task from its desktop shortcut."""
        s = self.step(step_id)
        n = len(s["attempts"]) + 1
        s["attempts"].append({"n": n, "started": self.now().isoformat(), "ended": None, "outcome": None,
                              "opened_by_hand": by_hand})
        s["status"] = STARTED
        self.event("REPEAT" if n > 1 else "START", step_id, f"attempt {n}" + (" (repeat)" if n > 1 else "")
                   + (" (task opened by hand)" if by_hand else ""))
        return n

    def _close_attempt(self, s, outcome):
        if s["attempts"] and s["attempts"][-1]["outcome"] is None:
            s["attempts"][-1].update(ended=self.now().isoformat(), outcome=outcome)

    def done(self, step_id):
        s = self.step(step_id)
        self._close_attempt(s, DONE)
        s["status"] = DONE
        self.event("DONE", step_id, f"after {len(s['attempts'])} attempt(s)")

    def problem(self, step_id, note):
        note = " ".join(str(note).split())
        if not note:
            raise ValueError("a problem needs a note")
        s = self.step(step_id)
        self._close_attempt(s, PROBLEM)
        s["status"] = PROBLEM
        s["notes"].append(note)
        self.event("PROBLEM", step_id, note)

    def skip(self, step_id, reason):
        reason = " ".join(str(reason).split())
        if not reason:
            raise ValueError("a skip needs a reason")
        s = self.step(step_id)
        self._close_attempt(s, SKIPPED)
        s["status"] = SKIPPED
        s["notes"].append("skipped: " + reason)
        self.event("SKIP", step_id, reason)

    def launch_failed(self, step_id, message):
        self.event("NOLAUNCH", step_id, message)

    def finish(self, reason):
        if self.ended is None:
            self.ended, self.end_reason = self.now(), reason
            self.event("END", None, reason)

    def reopen(self):
        """Back from the end screen: the session goes on."""
        if self.ended is not None:
            self.ended, self.end_reason = None, None
            self.event("REOPEN", None, "back from the end screen")

    def summary(self):
        """One row per step for the end screen: title, status, attempts, first start, last end, notes."""
        rows = []
        for s in self.steps:
            first = s["attempts"][0]["started"][11:16] if s["attempts"] else ""
            ends = [a["ended"] for a in s["attempts"] if a["ended"]]
            rows.append((s["title"], s["status"], len(s["attempts"]), first, ends[-1][11:16] if ends else "",
                         "; ".join(s["notes"])))
        return rows

    def to_dict(self):
        p = self.protocol
        return {"protocol": p["name"], "protocol_file": Path(p.get("file", "?")).name,
                "order_status": p.get("order_status"),
                "demo": self.demo, **self.who, "started": self.started.isoformat(),
                "ended": self.ended.isoformat() if self.ended else None, "end_reason": self.end_reason,
                "devices": self.devices, "run_order": self.run_order(), "steps": self.steps, "events": self.events}


# ---------------------------------------------------------------- command-line check (no window)

def check_cli(name):
    """Print what is not set up in a protocol on this PC; exit code 0 only when everything is."""
    try:
        protocol = load_protocol(name)
    except ProtocolError as e:
        print(e)
        return 2
    print(f"{protocol['file']}: {protocol['name']}, {len(protocol['steps'])} steps. {protocol['order_status']}")
    bad = 0
    for step in protocol["steps"]:
        launch = step["launch"]
        problem = launch_problem(launch)
        if launch is None:
            target = "nothing to open"
        elif "open" in launch:
            target = resolve_open(launch["open"])
        else:
            target = " ".join(launch["command"])
        print(f"  {'TODO' if problem else 'ok  '}  {step['id']}: {target}")
        if problem:
            print(f"        {problem}")
            bad += 1
    print("Everything is set up." if not bad else f"{bad} launch target(s) to fill in or fix.")
    return 1 if bad else 0


# ---------------------------------------------------------------- the window

FONT = ("Segoe UI", 12)
BOLD = ("Segoe UI", 12, "bold")
BIG = ("Segoe UI", 18, "bold")
HUGE = ("Segoe UI", 20, "bold")
SMALL = ("Segoe UI", 10)
SMALL_BOLD = ("Segoe UI", 10, "bold")
GREEN, RED, GREY, AMBER = "#1a7f37", "#c62828", "#666666", "#b26a00"
TYPE_BG = "#fff6cc"
CURRENT_BG = "#d6e4f5"
STATUS_FG = {NOT_STARTED: "black", STARTED: AMBER, DONE: GREEN, PROBLEM: RED, SKIPPED: GREY}
STEP_LIST_WIDTH = 230   # the step list on the left of every step screen (fits a 1366x768 laptop)


class App:
    def __init__(self, root, args):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk, self.root, self.args = tk, ttk, root, args
        self.session = None
        self.protocol = None
        self.index = 0
        self.ticks = {}            # step id -> [BooleanVar per prompt], kept when going back
        self.device_results = None
        self.scroll_canvas = None  # the step screen's scrolling area (None on other screens)
        root.title("Session Runner" + (" (DEMO)" if args.demo else ""))
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(1100, int(sw * 0.9)), int(sh * 0.85)
        top = (sh - h) // 4
        if sh < 900:   # a 768-pixel laptop screen: every pixel of height, above the taskbar
            h, top = sh - 100, 0
        root.geometry(f"{w}x{h}+{(sw - w) // 2}+{top}")
        self.wrap = w - 60
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind_all("<MouseWheel>", self.on_wheel)
        ttk.Style().configure("TButton", font=FONT, padding=8)
        self.header = tk.Label(root, font=BIG, anchor="w", justify="left", padx=12, pady=6, bg="#e8eef7")
        self.header.pack(fill="x")
        self.body = ttk.Frame(root, padding=14)
        self.body.pack(fill="both", expand=True)
        self.show_start()

    # -- helpers
    def clear(self):
        for child in self.body.winfo_children():
            child.destroy()
        self.update_header()

    def update_header(self):
        demo = "DEMO MODE (nothing real is started)   " if self.args.demo else ""
        if self.session is None:
            self.header.config(text=demo + "Session Runner", fg="black")
            return
        w = self.session.who
        text = f"{demo}Participant  {w['participant']}     Session  {w['session']}     RA  {w['initials']}"
        if self.session.log_error:
            text += "\n" + self.session.log_error
        self.header.config(text=text, fg=RED if self.session.log_error else "black")

    def label(self, parent, text, font=FONT, fg="black", wrap=None, **kw):
        lab = self.tk.Label(parent, text=text, font=font, fg=fg, anchor="w", justify="left",
                            wraplength=wrap or self.wrap, **kw)
        lab.pack(fill="x", anchor="w", pady=2)
        return lab

    def ask_line(self, title, prompt):
        """A required one-line note; None if cancelled."""
        from tkinter import simpledialog
        while True:
            text = simpledialog.askstring(title, prompt, parent=self.root)
            if text is None:
                return None
            if text.strip():
                return text.strip()

    def error(self, title, message):
        from tkinter import messagebox
        messagebox.showerror(title, message, parent=self.root)

    # -- screen 1: start
    def show_start(self):
        tk, ttk = self.tk, self.ttk
        self.clear()
        self.label(self.body, "New session", BIG)
        self.label(self.body, "Fill in all three, then click Next. They are shown on every screen after this.",
                   fg=GREY)
        form = ttk.Frame(self.body, padding=(0, 10))
        form.pack(anchor="w")
        names = protocol_names()
        self.protocol_var = tk.StringVar(value=self.args.protocol or (names[0] if len(names) == 1 else ""))
        self.vars = [tk.StringVar() for _ in range(3)]
        rows = [("Protocol", None), ("RA initials", self.vars[0]), ("Participant ID", self.vars[1]),
                ("Session number", self.vars[2])]
        for r, (text, var) in enumerate(rows):
            tk.Label(form, text=text, font=BOLD).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=6)
            if var is None:
                w = ttk.Combobox(form, textvariable=self.protocol_var, values=names, font=BIG, width=18,
                                 state="readonly")
            else:
                w = tk.Entry(form, textvariable=var, font=BIG, width=20)
            w.grid(row=r, column=1, sticky="w", pady=6)
            if r == 1:
                w.focus_set()
        self.label(self.body, "Participant ID: exactly 4 digits (e.g. 0007) when the session includes RRST; "
                              "otherwise letters and digits. Session: a number (1, 2, ...).", fg=GREY)
        self.start_msg = self.label(self.body, "", fg=RED)
        ttk.Button(self.body, text="Next  >", command=self.start_next).pack(anchor="w", pady=10)
        self.root.bind("<Return>", lambda e: self.start_next())

    def start_next(self):
        from tkinter import messagebox
        if not self.protocol_var.get():
            self.start_msg.config(text="Protocol: pick one from the list")
            return
        try:
            protocol = load_protocol(self.protocol_var.get(), demo=self.args.demo)
        except ProtocolError as e:
            self.start_msg.config(text=str(e))
            return
        who, problems = validate_start(*(v.get() for v in self.vars),
                                       digits_only=protocol["participant_digits_only"])
        if problems:
            self.start_msg.config(text="\n".join(problems))
            return
        if not messagebox.askyesno("Check", f"Protocol: {protocol['name']}\nRA: {who['initials']}\n"
                                   f"Participant: {who['participant']}\nSession: {who['session']}\n\nIs this correct?",
                                   parent=self.root):
            return
        self.root.unbind("<Return>")
        self.protocol = protocol
        self.session = Session(protocol, who, demo=self.args.demo)
        self.show_devices()

    # -- screen 2: devices
    def show_devices(self):
        ttk = self.ttk
        self.clear()
        self.label(self.body, "Device check", BIG)
        self.label(self.body, "Every light should be green. Fix a red one (the line says how), then Check again.",
                   fg=GREY)
        self.dev_frame = ttk.Frame(self.body, padding=(0, 8))
        self.dev_frame.pack(fill="x")
        buttons = ttk.Frame(self.body, padding=(0, 10))
        buttons.pack(fill="x")
        self.btn_recheck = ttk.Button(buttons, text="Check again", command=self.run_device_check)
        self.btn_recheck.pack(side="left", padx=(0, 10))
        self.btn_dev_ok = ttk.Button(buttons, text="Continue  >", command=self.devices_continue, state="disabled")
        self.btn_dev_ok.pack(side="left", padx=(0, 10))
        self.btn_dev_skip = ttk.Button(buttons, text="Continue anyway (give a reason)...", command=self.devices_skip,
                                       state="disabled")
        self.btn_dev_skip.pack(side="left")
        if not self.protocol["devices"]:
            self.device_results = {}
            self.label(self.dev_frame, "No devices to check in this protocol.")
            self.btn_dev_ok.config(state="normal")
            return
        self.run_device_check()

    def run_device_check(self):
        for child in self.dev_frame.winfo_children():
            child.destroy()
        self.label(self.dev_frame, "Checking ...", fg=GREY)
        for b in (self.btn_recheck, self.btn_dev_ok, self.btn_dev_skip):
            b.config(state="disabled")
        names = list(self.protocol["devices"])
        box = {}

        def work():
            try:
                box["r"] = check_devices(names)
            except Exception as e:  # noqa: BLE001
                box["r"] = {n: (False, f"could not check ({e})") for n in names}

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self.root.after(100, self.poll_device_check, thread, box)

    def poll_device_check(self, thread, box):
        if thread.is_alive():
            self.root.after(100, self.poll_device_check, thread, box)
            return
        if not self.dev_frame.winfo_exists():
            return
        tk = self.tk
        for child in self.dev_frame.winfo_children():
            child.destroy()
        self.device_results = box["r"]
        for r, (name, (ok, detail)) in enumerate(self.device_results.items()):
            tk.Label(self.dev_frame, text="\u25cf", font=("Segoe UI", 26), fg=GREEN if ok else RED).grid(
                row=r, column=0, padx=(0, 10))
            tk.Label(self.dev_frame, text=DEVICES[name][0], font=BOLD).grid(row=r, column=1, sticky="w")
            tk.Label(self.dev_frame, text=detail, font=FONT, fg="black" if ok else RED, wraplength=600,
                     justify="left").grid(row=r, column=2, sticky="w", padx=12)
        all_ok = all(ok for ok, _ in self.device_results.values())
        self.btn_recheck.config(state="normal")
        self.btn_dev_ok.config(state="normal" if all_ok else "disabled")
        self.btn_dev_skip.config(state="disabled" if all_ok else "normal")

    def devices_continue(self):
        self.session.record_devices(self.device_results)
        self.show_step(0)

    def devices_skip(self):
        reason = self.ask_line("Continue without all green", "Why continue? (one line, goes into the session log)")
        if reason:
            self.session.record_devices(self.device_results, skip_reason=reason)
            self.show_step(0)

    # -- screen 3: one per step
    def show_step(self, index):
        tk, ttk = self.tk, self.ttk
        self.index = index
        step = self.protocol["steps"][index]
        self.clear()
        # buttons first, at the bottom, so a small screen never hides them
        buttons = ttk.Frame(self.body, padding=(0, 8))
        buttons.pack(side="bottom", fill="x")
        self.step_buttons = []
        wrap = self.wrap
        if len(self.protocol["steps"]) > 1:
            self.show_step_list()
            wrap = self.wrap - STEP_LIST_WIDTH - 20
        main = self.scroll_area(self.body)
        title_row = ttk.Frame(main)   # "Step 2 of 4" beside the title: one line less on a 768-pixel screen
        title_row.pack(fill="x", pady=(0, 2))
        tk.Label(title_row, text=f"Step {index + 1} of {len(self.protocol['steps'])}", font=FONT, fg=GREY).pack(
            side="right", anchor="n", padx=(12, 0))
        tk.Label(title_row, text=step["title"], font=BIG, anchor="w", justify="left", wraplength=wrap - 120).pack(
            side="left", fill="x")
        self.label(main, step["instructions"], wrap=wrap)
        ticks = self.ticks.setdefault(step["id"], [tk.BooleanVar() for _ in step["prompts"]])
        if step["prompts"]:
            self.label(main, "Tick each one before you press Start:", BOLD).pack_configure(pady=(8, 2))
            for text, var in zip(step["prompts"], ticks):
                tk.Checkbutton(main, text=text, variable=var, font=FONT, anchor="w", justify="left", bd=0, pady=1,
                               wraplength=wrap - 30, command=self.update_step_buttons).pack(fill="x", anchor="w")
        if step["type_into_task"]:
            box = tk.Frame(main, bg=TYPE_BG, padx=12, pady=6, highlightthickness=1, highlightbackground=AMBER)
            box.pack(fill="x", pady=(10, 4))
            tk.Label(box, text="Type exactly this into the task when it asks:", font=BOLD, bg=TYPE_BG).grid(
                row=0, column=0, columnspan=2, sticky="w")
            for r, (what, value) in enumerate(step["type_into_task"], 1):
                tk.Label(box, text=what, font=FONT, bg=TYPE_BG, bd=0, pady=0).grid(row=r, column=0, sticky="w",
                                                                                   padx=(0, 16))
                tk.Label(box, text=fill(value, self.session.who), font=HUGE, bg=TYPE_BG, bd=0, pady=0).grid(
                    row=r, column=1, sticky="w")
        problem = launch_problem(step["launch"])
        if problem:
            self.label(main, problem, BOLD, fg=RED, wrap=wrap)
        self.step_status = self.label(main, "", BOLD, wrap=wrap)
        self.step_status.pack_configure(pady=(6, 0))
        self.btn_back = ttk.Button(buttons, text="<  Back", command=self.step_back)
        self.btn_back.pack(side="left", padx=(0, 16))
        self.btn_start = ttk.Button(buttons, text="Start", command=self.step_start)
        self.btn_start.pack(side="left", padx=(0, 8))
        self.btn_done = ttk.Button(buttons, text="Done", command=self.step_done)
        self.btn_done.pack(side="left", padx=(0, 8))
        self.btn_problem = ttk.Button(buttons, text="Problem...", command=self.step_problem)
        self.btn_problem.pack(side="left", padx=(0, 8))
        self.btn_next = ttk.Button(buttons, text="Next step  >", command=self.step_next)
        self.btn_next.pack(side="right")
        self.btn_skip = ttk.Button(buttons, text="Skip step...", command=self.step_skip)
        self.btn_skip.pack(side="right", padx=(0, 8))
        self.update_step_buttons()

    def update_step_buttons(self):
        step = self.protocol["steps"][self.index]
        state = self.session.step(step["id"])
        ticked = all(v.get() for v in self.ticks[step["id"]])
        attempts = state["attempts"]
        self.btn_start.config(text="Start again (repeat)" if attempts else "Start",
                              state="normal" if ticked else "disabled")
        self.btn_done.config(state="normal" if attempts and state["status"] != DONE else "disabled")
        self.btn_problem.config(state="normal" if attempts else "disabled")
        self.btn_next.config(state="normal" if state["status"] in (DONE, PROBLEM, SKIPPED) else "disabled")
        self.btn_back.config(state="normal" if self.index > 0 else "disabled")
        last = attempts[-1] if attempts else None
        status = state["status"]
        if status == NOT_STARTED:
            text, fg = ("Tick every box, then press Start." if not ticked else "Ready: press Start."), GREY
        elif status == STARTED:
            text, fg = (f"Started at {last['started'][11:16]} (attempt {last['n']}). Press Done when it has "
                        "finished, or Problem if something went wrong."), AMBER
        elif status == DONE:
            text, fg = f"Done at {last['ended'][11:16] if last and last['ended'] else '?'}. Press Next step.", GREEN
        elif status == PROBLEM:
            text, fg = (f"Problem noted: {state['notes'][-1]}\nStart again (repeat) once it is fixed, or Next step "
                        "to go on."), RED
        else:
            text, fg = f"Skipped: {state['notes'][-1][len('skipped: '):]}. Press Next step.", GREY
        self.step_status.config(text=text, fg=fg)
        self.refresh_step_list()
        self.update_header()

    def scroll_area(self, parent):
        """A frame that gets a scroll bar only when its content is taller than the window (a step with many
        lines on a 768-pixel screen), so nothing is ever cut off unseen."""
        tk, ttk = self.tk, self.ttk
        outer = ttk.Frame(parent)
        outer.pack(side="left", fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0, bd=0, yscrollincrement=20)
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas)
        item = canvas.create_window(0, 0, window=inner, anchor="nw")

        def layout(_event=None):
            canvas.itemconfigure(item, width=canvas.winfo_width())
            canvas.configure(scrollregion=(0, 0, canvas.winfo_width(), inner.winfo_reqheight()))
            if inner.winfo_reqheight() > canvas.winfo_height() + 2:
                if not bar.winfo_ismapped():
                    bar.pack(side="right", fill="y", before=canvas)
            elif bar.winfo_ismapped():
                bar.pack_forget()
                canvas.yview_moveto(0)

        inner.bind("<Configure>", layout)
        canvas.bind("<Configure>", layout)
        self.scroll_canvas, self.scroll_bar = canvas, bar
        return inner

    def on_wheel(self, event):
        canvas = self.scroll_canvas
        if canvas is not None and canvas.winfo_exists() and self.scroll_bar.winfo_ismapped():
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def show_step_list(self):
        """Every step with its status, on the left; clicking one opens it (any order). The current one is
        highlighted. The protocol's order is the default; this is for doing the steps in another order."""
        tk = self.tk
        panel = tk.Frame(self.body, width=STEP_LIST_WIDTH)
        panel.pack(side="left", fill="y", padx=(0, 20))
        panel.pack_propagate(False)
        tk.Label(panel, text="All steps (click one to open it)", font=SMALL, fg=GREY, anchor="w").pack(fill="x")
        self.step_buttons = []
        for i in range(len(self.protocol["steps"])):
            b = tk.Button(panel, anchor="w", justify="left", wraplength=STEP_LIST_WIDTH - 20, padx=8, pady=3,
                          cursor="hand2", command=lambda i=i: self.step_goto(i))
            b.pack(fill="x", pady=2)
            self.step_buttons.append(b)
        self.refresh_step_list()

    def refresh_step_list(self):
        for i, (b, step) in enumerate(zip(self.step_buttons, self.protocol["steps"])):
            status = self.session.step(step["id"])["status"]
            current = i == self.index
            b.config(text=f"{i + 1}. {step['title']}\n     {status}", fg=STATUS_FG[status],
                     font=SMALL_BOLD if current else SMALL, relief="sunken" if current else "raised",
                     bg=CURRENT_BG if current else "#f4f4f4", activeforeground=STATUS_FG[status])

    def step_goto(self, index):
        if index == self.index:
            return
        steps = self.protocol["steps"]
        self.session.goto(steps[self.index]["id"], steps[index]["id"])
        self.show_step(index)

    def step_start(self):
        from tkinter import messagebox
        step = self.protocol["steps"][self.index]
        state = self.session.step(step["id"])
        if state["attempts"] and not messagebox.askyesno(
                "Start again?", f"{step['title']} was already started at {state['attempts'][-1]['started'][11:16]}.\n\n"
                "Start it again? Only if the task has closed or failed. It is logged as a repeat.", parent=self.root):
            return
        by_hand = False
        if step["launch"] is not None:
            try:
                start_launch(step["launch"])
            except OSError as e:
                self.session.launch_failed(step["id"], str(e))
                if not messagebox.askyesno("Could not start the task", f"{e}\n\nOpen the task from its desktop "
                                           "shortcut now. Click Yes once it is open: this step goes on (logged as "
                                           "opened by hand). No: stay on this screen.", icon="warning",
                                           parent=self.root):
                    self.update_step_buttons()
                    return
                by_hand = True
        self.session.start(step["id"], by_hand=by_hand)
        self.update_step_buttons()

    def step_done(self):
        self.session.done(self.protocol["steps"][self.index]["id"])
        self.step_next()

    def step_problem(self):
        note = self.ask_line("Problem", "What went wrong? (one line, goes into the session log)")
        if note:
            self.session.problem(self.protocol["steps"][self.index]["id"], note)
            self.update_step_buttons()

    def step_skip(self):
        reason = self.ask_line("Skip step", "Why skip this step? (one line, goes into the session log)")
        if reason:
            self.session.skip(self.protocol["steps"][self.index]["id"], reason)
            self.step_next()

    def step_back(self):
        if self.index > 0:
            self.show_step(self.index - 1)

    def step_next(self):
        if self.index + 1 < len(self.protocol["steps"]):
            self.show_step(self.index + 1)
        else:
            self.show_end()

    # -- screen 4: end
    def show_end(self):
        ttk = self.ttk
        self.session.finish(self.session.completion())
        self.clear()
        self.label(self.body, "Session finished", BIG)
        d = self.session.devices or {}
        dev = "all green" if d.get("all_ok") else f"continued without all green: {d.get('skip_reason')}"
        self.label(self.body, f"Started {self.session.started:%H:%M}, finished {self.session.ended:%H:%M}. "
                              f"Device check: {dev}.")
        ttk.Style().configure("Treeview", font=FONT, rowheight=30)
        ttk.Style().configure("Treeview.Heading", font=BOLD)
        cols = ("Step", "Status", "Attempts", "Started", "Ended", "Notes")
        table = ttk.Treeview(self.body, columns=cols, show="headings", height=len(self.session.steps))
        for c, w in zip(cols, (260, 90, 80, 70, 70, 400)):
            table.heading(c, text=c)
            table.column(c, width=w, anchor="w")
        for row in self.session.summary():
            table.insert("", "end", values=row)
        table.pack(fill="x", pady=10)
        titles = {s["id"]: s["title"] for s in self.protocol["steps"]}
        order = self.session.run_order()
        if order:
            self.label(self.body, "Order the steps were started in: " + ",  ".join(titles[i] for i in order))
        problems = [r for r in self.session.summary() if r[1] in (PROBLEM, SKIPPED, NOT_STARTED, STARTED)]
        if problems:
            self.label(self.body, "Some steps were not done: tell the researcher (the log has the details).", BOLD,
                       fg=RED)
        self.label(self.body, f"Session log: {self.session.txt_path}", fg=GREY)
        buttons = ttk.Frame(self.body, padding=(0, 10))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="<  Back to the last step", command=self.end_back).pack(side="left", padx=(0, 10))
        ttk.Button(buttons, text="Close", command=self.root.destroy).pack(side="left")

    def end_back(self):
        self.session.reopen()
        self.show_step(self.index)

    # -- closing and errors
    def on_close(self):
        from tkinter import messagebox
        if self.session is not None and self.session.ended is None:
            if not messagebox.askyesno("Close?", "Close the Session Runner before the end?\n\nThe session log so far is "
                                       "kept. Tasks that are running keep running.", parent=self.root):
                return
            step = self.protocol["steps"][self.index]["id"]
            self.session.finish(f"closed by the RA before the end (at step {step})")
        self.root.destroy()

    def tk_error(self, exc, value, tb):
        """Any unexpected error: a plain message on screen, details to a file. Never a traceback window."""
        text = "".join(traceback.format_exception(exc, value, tb))
        where = write_error(text, self.protocol["log_dir"] if self.protocol else None)
        if self.session is not None:
            self.session.event("ERROR", None, f"{exc.__name__}: {value} (details: {where})")
        self.error("Something went wrong", f"The Session Runner hit an unexpected error: {value}\n\n"
                   f"The session log is kept. Details for the researcher: {where}")


def write_error(text, log_dir=None):
    """Append a traceback to session-runner_errors.txt in the log folder; returns the path (or a note)."""
    folder = Path(log_dir) if log_dir else ROOT / DEFAULT_LOG_DIR
    path = folder / "session-runner_errors.txt"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"--- {clock().isoformat()}\n{text}\n")
        return str(path)
    except OSError as e:
        return f"(could not write {path}: {e})"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--protocol", help="a protocol name in protocols/ (full-battery) or a .json path")
    ap.add_argument("name", nargs="?", help="same as --protocol")
    ap.add_argument("--demo", action="store_true", help="every launch opens Notepad; log files end in _demo")
    ap.add_argument("--check", metavar="PROTOCOL", help="no window: list launch targets still to fill in")
    args = ap.parse_args(argv)
    args.protocol = args.protocol or args.name
    if args.check:
        return check_cli(args.check)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    import tkinter as tk
    root = tk.Tk()
    try:
        app = App(root, args)
    except Exception:  # noqa: BLE001
        where = write_error(traceback.format_exc())
        from tkinter import messagebox
        messagebox.showerror("Session Runner", f"The Session Runner could not start. Details: {where}")
        return 1
    root.report_callback_exception = app.tk_error
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
