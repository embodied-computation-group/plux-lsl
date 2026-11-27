"""Physio Recorder: check PLUX signals, then record a timed window behind a fixation screen.

Start it from the desktop shortcut, or:
    .venv\\Scripts\\pythonw.exe physio_app.py [--sub 01 --ses 1 --recording restbaseline --minutes 15]
The optional arguments pre-fill the form (for a future experiment runner).

Workflow: 1) Check signals -> fix anything not green.  2) Start recording ->
participant screen shows instructions; RA presses Space to begin, the
participant sees a fixation cross for the whole window; Esc twice stops early.
3) PASS / CHECK summary for the file just saved.
"""

import argparse
import ctypes
import datetime
import logging
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import lsl_quiet  # noqa: F401  (must precede pylsl)
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from pylsl import StreamInfo, StreamInlet, StreamOutlet, cf_string, local_clock, resolve_byprop

from check_xdf import assess
from devices import auto_hub, format_port_kinds, load_devices, normalize_mac, parse_hub_lines, save_devices
from monitor import BAD, OK, WARN, Monitor, draw, find_plux, find_stream
from auto_stop import END_GRACE_S, AutoStop, is_end_marker
from recorder import (DATA_DIR, LOG_DIR, ROOT, already_recorded_text, app_log_path, labrecorder_log_path,
                      output_path, previous_recording, start_recorder, stop_recorder)
from task_runs import FormSettle, TaskRuns

# shown name -> file label; minutes (None = task: no fixation screen, runs until the task ends);
# the task's LSL marker stream that must be online before recording starts; whether the
# RRST airflow sensor ("Flow" stream) is expected; and for tasks, the prefix of the task's
# end marker (compared with the start of the marker's first word; the recording then stops
# by itself, END_GRACE_S later).
PRESETS = {
    "Rest - baseline": {"task": "restbaseline", "minutes": 15, "markers": None, "flow": False},
    "Rest - peak": {"task": "restpeak", "minutes": 15, "markers": None, "flow": False},
    "RRST": {"task": "rrst", "minutes": None, "markers": "RRST", "flow": True,
             "end": "session_end"},           # session_end_<status>
    "Effort task (dynamometer)": {"task": "effortreward", "minutes": None, "markers": "EffortReward",
                                  "flow": False, "end": "task_end"},     # task_end, task_end_early
    "HRD": {"task": "hrd", "minutes": None, "markers": "HRD", "flow": False,
            "end": "HRD_end"},                # HRD_end, HRD_end_early <reason>
}
LEAD_IN_S = 3        # recorder warm-up before the participant screen appears
LOST_S = 10          # no data for this long during a window = connection lost
EXTRA_STREAMS = {"hrd": ("Nonin",)}  # task -> other streams recorded when online (HRD's Nonin PPG)
FIGURES = {"rrst": "rrst_figures.py"}  # task -> script run after Stop: <xdf> -> <stem>_figures/
TASK_RESOLVE_S = 3  # how often to look for the task's trigger stream while checking
INSTRUCTIONS = ("Please sit comfortably and keep as still as you can.\n\n"
                "Breathe normally and keep your eyes on the cross.\n\n"
                "The researcher will tell you when it is finished.")
START_REST = "2   Start recording"
START_TASK = "2   Start manually (only if it did not start by itself)"  # tasks start recording by themselves
FONT = ("Segoe UI", 11)
BIG = ("Segoe UI", 14, "bold")

log = logging.getLogger("physio_app")
LOG_FILE = app_log_path()  # one per app start: logs/app_YYYYMMDD-HHMMSS.log


def find_hubs_now():
    """Ask plux_lsl.py --find for the biosignalsplux hubs Windows knows (takes ~10 s). Returns
    ([(MAC, description)], error text or None). A separate process keeps PLUX's API out of the app."""
    try:
        out = subprocess.run([str(ROOT / ".venv" / "Scripts" / "python.exe"), str(ROOT / "plux_lsl.py"), "--find"],
                             capture_output=True, text=True, timeout=60, cwd=ROOT,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.error("hub search failed: %s", exc)
        return [], f"the Bluetooth search failed: {exc}"
    log.info("hub search: %s | %s", out.stdout.strip(), out.stderr.strip()[-600:])
    if out.returncode != 0:
        return [], "the Bluetooth search failed: " + (out.stderr.strip().splitlines() or ["?"])[-1]
    return parse_hub_lines(out.stdout), None


def battery_text(hub_log):
    """(hub line text, colour) from plux_lsl.py's log. No battery level usually means the hub is on
    the charger, which must never touch a participant: red, like a low battery."""
    battery = re.search(r"Battery: (\d+)%", hub_log)
    if battery is None:
        return "battery unknown: UNPLUG THE HUB FROM THE CHARGER before sensors touch the participant", BAD
    return f"battery {battery.group(1)}%", BAD if int(battery.group(1)) < 20 else "black"


def label_to_task(text):
    if text in PRESETS:
        return PRESETS[text]["task"]
    return re.sub(r"[^A-Za-z0-9]", "", text).lower()


class ParticipantScreen(tk.Toplevel):
    """Fullscreen participant view: instructions -> fixation cross -> finished."""

    def __init__(self, master, on_begin, on_abort):
        super().__init__(master, bg="#808080", cursor="none")
        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.canvas = tk.Canvas(self, bg="#808080", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.on_begin, self.on_abort = on_begin, on_abort
        self.phase = "instructions"
        self.last_esc = 0.0
        self.confirming = False
        self.bind("<space>", self._space)
        self.bind("<Escape>", self._escape)
        self.bind("<Key>", self._key, add="+")
        self.bind("<Configure>", lambda e: self._redraw())
        self.after(100, self._grab)

    def _grab(self):
        self.focus_force()
        self._redraw()

    def _redraw(self):
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if self.phase == "instructions":
            c.create_text(w / 2, h / 2, text=INSTRUCTIONS, fill="black", font=("Segoe UI", 24),
                          justify="center", width=w * 0.7)
            c.create_text(w / 2, h * 0.92, text="Researcher: press Space to start", fill="#404040",
                          font=("Segoe UI", 12))
        elif self.phase == "fixation":
            arm, thick = min(w, h) * 0.025, max(3, int(min(w, h) * 0.004))
            c.create_line(w / 2 - arm, h / 2, w / 2 + arm, h / 2, width=thick, fill="black")
            c.create_line(w / 2, h / 2 - arm, w / 2, h / 2 + arm, width=thick, fill="black")
            if self.confirming:
                c.create_text(w / 2, h * 0.92, text="Researcher: stop the recording?  Y = stop,  "
                              "any other key = keep recording", fill="#202020", font=("Segoe UI", 14))
        else:
            c.create_text(w / 2, h / 2, text="Finished - thank you.\n\nPlease wait for the researcher.",
                          fill="black", font=("Segoe UI", 24), justify="center")

    def _space(self, _):
        if self.phase == "instructions":
            self.phase = "fixation"
            self._redraw()
            self.on_begin()

    def _escape(self, _):
        now = time.monotonic()
        if self.phase == "instructions" and now - self.last_esc < 2:
            self.on_abort()  # nothing recorded yet, no need to confirm
        elif self.phase == "fixation" and not self.confirming and now - self.last_esc < 2:
            self.confirming = True
            self._redraw()
        self.last_esc = now

    def _key(self, event):
        if not self.confirming or event.keysym == "Escape":
            return
        self.confirming = False
        self._redraw()
        if event.keysym.lower() == "y":
            self.on_abort()

    def finished(self):
        self.phase = "finished"
        self._redraw()
        self.after(3000, self.destroy)


class HubDialog(tk.Toplevel):
    """Pick this PC's PLUX hub: one found over Bluetooth, or a MAC typed in. hubs=None searches
    first. on_done(MAC) runs on OK with a valid MAC; on_cancel() when closed without one."""

    def __init__(self, master, current, on_done, on_cancel=None, hubs=None, note=""):
        super().__init__(master, padx=12, pady=10)
        self.title("PLUX hub")
        self.transient(master)
        self.resizable(False, False)
        self.on_done, self.on_cancel, self.hubs = on_done, on_cancel, []
        ttk.Label(self, text=note or "Which PLUX hub does this PC use?").pack(anchor="w")
        self.status = ttk.Label(self, text="", foreground="#666")
        self.status.pack(anchor="w", pady=(4, 0))
        self.listbox = tk.Listbox(self, height=4, width=44, font=FONT, exportselection=False)
        self.listbox.pack(fill="x", pady=4)
        self.listbox.bind("<<ListboxSelect>>", self._picked)
        ttk.Label(self, text="or type its MAC address (on the hub's label), e.g. 00:07:80:4B:3F:25").pack(anchor="w")
        self.mac = tk.StringVar(value=current or "")
        ttk.Entry(self, textvariable=self.mac, width=24, font=FONT).pack(anchor="w", pady=4)
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="OK", command=self._ok).pack(side="right")
        ttk.Button(row, text="Cancel", command=self._cancel).pack(side="right", padx=8)
        self.btn_search = ttk.Button(row, text="Search again", command=self._search)
        self.btn_search.pack(side="left")
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        if hubs is None:
            self._search()
        else:
            self._show(hubs, None)

    def _search(self):
        self.btn_search.config(state="disabled")
        self.status.config(text="Looking for paired hubs ... (a few seconds)")

        def search():
            hubs, error = find_hubs_now()
            try:
                self.after(0, lambda: self._show(hubs, error))
            except RuntimeError:  # dialog or app already closed
                pass
        threading.Thread(target=search, daemon=True).start()

    def _show(self, hubs, error):
        if not self.winfo_exists():
            return
        self.btn_search.config(state="normal")
        self.hubs = hubs
        self.listbox.delete(0, "end")
        for mac, desc in hubs:
            self.listbox.insert("end", f"{mac}   {desc}")
        self.status.config(text=error or (f"Paired hubs found: {len(hubs)}. Click one, then OK." if hubs else
                                          "No paired hub found: pair it in Windows Bluetooth settings "
                                          "and Search again, or type its MAC."))

    def _picked(self, _):
        sel = self.listbox.curselection()
        if sel:
            self.mac.set(self.hubs[sel[0]][0])

    def _ok(self):
        mac = normalize_mac(self.mac.get())
        if mac is None:
            messagebox.showwarning("PLUX hub", "A MAC address is six pairs of 0-9 / A-F, "
                                   "e.g. 00:07:80:4B:3F:25.", parent=self)
            return
        self.destroy()
        self.on_done(mac)

    def _cancel(self):
        self.destroy()
        if self.on_cancel is not None:
            self.on_cancel()


class App:
    def __init__(self, root, args):
        self.root = root
        self.bridge = None
        self.bridge_log = None
        self.monitor = None
        self.flow_axes = []
        self.flow = None      # {"proc", "log", "monitor", "status": connecting|ok|failed, "msg"}
        self.recorder = None
        self.screen = None
        self.state = "idle"   # idle | connecting | checking | starting | recording | saving
        self.path = None
        self.task = None
        self.start_seq = self.end_seq = None
        self.next_log = 0
        self.task_name = None   # the task's trigger stream being watched (preset "markers")
        self.runs = TaskRuns()  # its runs (uids) and which were recorded: each run auto-starts once
        self.task_resolving = False
        self.next_task_resolve = 0
        self.session_on = False  # Check signals done and not disconnected (with or without the hub)
        self.rec_info = None    # {"sub", "ses", "since", "markers", "count", "last", ...}: state line, auto-stop
        self.auto_stop = None   # AutoStop while a task with an end marker is recorded
        # Banner lines kept on top of the routine banner, which _tick rewrites every 200 ms:
        self.alert = None        # urgent (red) while its cause lasts: a task started but could not auto-start
        self.banner_note = None  # (text, colour): "Recording stopped AUTOMATICALLY", "REPEAT"; until the
        #                          next recording, Check signals or form change
        self.form_settle = FormSettle()  # an auto-start waits until the form is unchanged for a moment
        self.auto_blocked = None  # uid of the run whose auto-start is held back (logged and beeped once)
        self.marker_stop = None  # threading.Event that stops the task-marker counter
        self.error_text, self.error_until = "", 0.0  # internal error note kept on the banner
        self.last_error, self.last_error_t = None, 0.0
        self.finding = None     # the hub search started by Check signals, else None
        self.markers = StreamOutlet(StreamInfo("SessionMarkers", "Markers", 1, 0, cf_string,
                                               "plux-lsl-session"))

        root.title("Physio Recorder")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = int(sw * 0.85), int(sh * 0.85)
        root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        style = ttk.Style()
        style.configure("TButton", font=FONT, padding=6)
        style.configure("TLabel", font=FONT)

        form = ttk.Frame(root, padding=10)
        form.pack(fill="x")
        self.sub = tk.StringVar(value=args.sub or "")
        self.ses = tk.StringVar(value=args.ses or "")
        self.recording = tk.StringVar(value=args.recording or next(iter(PRESETS)))
        self.minutes = tk.StringVar(value=str(args.minutes or ""))
        self.form_fields = []  # disabled while a recording starts, runs or is saved
        for col, (text, var, width) in enumerate([("Participant", self.sub, 8), ("Session", self.ses, 5)]):
            ttk.Label(form, text=text).grid(row=0, column=2 * col, sticky="w", padx=(0, 4))
            entry = ttk.Entry(form, textvariable=var, width=width, font=FONT)
            entry.grid(row=0, column=2 * col + 1, padx=(0, 16))
            self.form_fields.append(entry)
        ttk.Label(form, text="Recording").grid(row=0, column=4, sticky="w", padx=(0, 4))
        combo = ttk.Combobox(form, textvariable=self.recording, values=list(PRESETS), width=24, font=FONT)
        combo.grid(row=0, column=5, padx=(0, 16))
        combo.bind("<<ComboboxSelected>>", self.on_preset)
        self.form_fields.append(combo)
        ttk.Label(form, text="Minutes").grid(row=0, column=6, sticky="w", padx=(0, 4))
        self.min_box = ttk.Spinbox(form, textvariable=self.minutes, from_=1, to=120, width=5, font=FONT)
        self.min_box.grid(row=0, column=7)
        self.min_note = ttk.Label(form, text="", foreground="#666")
        self.min_note.grid(row=0, column=8, padx=(8, 0))
        devices = load_devices()
        self.use_plux = tk.BooleanVar(value=devices["plux"])
        self.use_flow = tk.BooleanVar(value=devices["flow"])
        dev_row = ttk.Frame(form)
        dev_row.grid(row=1, column=0, columnspan=9, sticky="w", pady=(8, 0))
        ttk.Label(dev_row, text="Devices").pack(side="left", padx=(0, 8))
        self.dev_boxes = [ttk.Checkbutton(dev_row, text=text, variable=var, command=self.on_devices)
                          for text, var in [("PLUX hub (ECG, breathing belt)", self.use_plux),
                                            ("Flow sensor (RRST)", self.use_flow)]]
        for box in self.dev_boxes:
            box.pack(side="left", padx=(0, 16))
        self.hub = devices["hub"]  # this PC's hub MAC; None = look for it at Check signals
        self.hub_label = ttk.Label(dev_row, text="")
        self.hub_label.pack(side="left", padx=(8, 4))
        ttk.Button(dev_row, text="Change", command=self.change_hub).pack(side="left")
        self.show_hub()

        buttons = ttk.Frame(root, padding=(10, 0))
        buttons.pack(fill="x")
        self.btn_check = ttk.Button(buttons, text="1   Check signals", command=self.check_signals)
        self.btn_check.pack(side="left")
        self.btn_record = ttk.Button(buttons, text=START_REST, command=self.start_recording,
                                     state="disabled")
        self.btn_record.pack(side="left", padx=10)
        self.btn_disconnect = ttk.Button(buttons, text="Disconnect", command=self.disconnect, state="disabled")
        self.btn_disconnect.pack(side="right")
        self.btn_retry = ttk.Button(buttons, text="Retry flow sensor", command=self.retry_flow)

        self.hub_status = ttk.Label(root, text="Hub: not connected", padding=(10, 6, 10, 0))
        self.hub_status.pack(fill="x")
        # One line that always says whether a recording is running; it ticks while it is.
        self.state_line = tk.Label(root, text="", font=("Segoe UI", 12, "bold"), anchor="w", padx=10)
        self.state_line.pack(fill="x")
        self.banner = tk.Label(root, text="Fill in participant and session, then click Check signals.",
                               font=BIG, anchor="w", padx=10, pady=6)
        self.banner.pack(fill="x")
        self.banner.bind("<Configure>", lambda e: self.banner.config(wraplength=e.width - 20))

        ttk.Label(root, text=f"Data folder: {DATA_DIR}", foreground="#666",
                  padding=(10, 0, 10, 6)).pack(side="bottom", fill="x")
        body = tk.Frame(root)
        body.pack(fill="both", expand=True)
        side = tk.Frame(body, width=int(w * 0.3), padx=10, pady=6, bd=1, relief="groove")
        side.pack(side="right", fill="y", padx=(0, 8), pady=6)
        side.pack_propagate(False)
        tk.Label(side, text="Last recording", font=BIG).pack(anchor="w")
        self.summary = tk.Frame(side)
        self.summary.pack(fill="both", expand=True)
        self.summary_wrap = int(w * 0.3) - 30
        self.clear_summary()

        self.fig = Figure(layout="constrained")
        self.canvas = FigureCanvasTkAgg(self.fig, master=body)
        self.canvas.get_tk_widget().pack(side="left", fill="both", expand=True, padx=6, pady=6)
        self.axes = []
        if not args.minutes:
            self.on_preset(None)
        for var in (self.sub, self.ses, self.recording):
            var.trace_add("write", self.on_form_change)
        self.btn_record.config(text=self.start_text())
        root.after(200, self.tick)

    # ---------- form ----------
    def preset(self):
        return PRESETS.get(self.recording.get().strip())

    def set_form_enabled(self, enabled):
        """Participant, Session and Recording: editable while idle or checking only."""
        for field in self.form_fields:
            field.config(state="normal" if enabled else "disabled")

    def start_text(self):
        preset = self.preset()
        return START_TASK if preset and preset["markers"] else START_REST

    def on_form_change(self, *_):
        """Participant, session or recording edited (the entries are disabled while recording)."""
        if self.state in ("starting", "recording", "saving"):
            return
        self.btn_record.config(text=self.start_text())
        if self.banner_note is not None:  # it was about the last recording
            self.banner_note = None
            self.set_banner(self.banner.cget("text").split("\n")[-1], self.banner.cget("fg"))

    def on_preset(self, _):
        preset = self.preset()
        if preset is None:
            return
        if preset["minutes"] is None:
            self.minutes.set("")
            self.min_box.config(state="disabled")
            self.min_note.config(text=f"stops when the task ends. Start {preset['markers']} first.")
        else:
            self.minutes.set(str(preset["minutes"]))
            self.min_box.config(state="normal")
            self.min_note.config(text="")
        if preset["flow"]:
            self.btn_retry.pack(side="right", padx=10)
        else:
            self.btn_retry.pack_forget()
        if self.state == "checking":
            self.ensure_flow()

    def on_devices(self):
        """Remember which devices this PC has. Only while disconnected."""
        if self.state != "idle":
            saved = load_devices()
            self.use_plux.set(saved["plux"])
            self.use_flow.set(saved["flow"])
            messagebox.showinfo("Devices", "Click Disconnect first, then change the devices.")
            return
        devices = save_devices({"plux": self.use_plux.get(), "flow": self.use_flow.get()})
        log.info("devices: %s", devices)

    def show_hub(self):
        self.hub_label.config(text=f"Hub: {self.hub}" if self.hub else "Hub: not set yet (found at Check signals)")

    def set_hub(self, mac):
        self.hub = mac
        save_devices({"hub": mac})
        log.info("hub set to %s", mac)
        self.show_hub()

    def change_hub(self):
        """Pick another hub (found over Bluetooth, or typed in). Only while disconnected, like the tick boxes."""
        if self.state != "idle":
            messagebox.showinfo("PLUX hub", "Click Disconnect first, then change the hub.")
            return
        HubDialog(self.root, self.hub, self.set_hub)

    def flow_expected(self):
        preset = self.preset()
        return bool(preset and preset["flow"] and self.use_flow.get())

    def form_values(self):
        sub, ses = self.sub.get().strip(), self.ses.get().strip()
        task = label_to_task(self.recording.get().strip())
        problems = []
        if not re.fullmatch(r"[A-Za-z0-9]+", sub):
            problems.append("Participant: letters/numbers only, e.g. 01")
        if not re.fullmatch(r"[A-Za-z0-9]+", ses):
            problems.append("Session: letters/numbers only, e.g. 1")
        if not task:
            problems.append("Recording: choose one or type a name")
        preset = self.preset()
        minutes = None
        if preset is None or preset["minutes"] is not None:
            try:
                minutes = float(self.minutes.get())
                if not 0 < minutes <= 180:
                    raise ValueError
            except ValueError:
                problems.append("Minutes: a number between 1 and 180")
        return sub, ses, task, minutes, problems

    # ---------- device connections ----------
    @staticmethod
    def launch_streamer(script, prefix, *args):
        """Start plux_lsl.py / flow_lsl.py hidden, logging to logs/. Returns (process, log path)."""
        LOG_DIR.mkdir(exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = LOG_DIR / f"{prefix}_{stamp}.log"
        proc = subprocess.Popen(
            [str(ROOT / ".venv" / "Scripts" / "python.exe"), str(ROOT / script), "--stop-on-eof", *args],
            stdin=subprocess.PIPE, stdout=open(log_path, "w"), stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW, cwd=ROOT, env=dict(os.environ, PYTHONUNBUFFERED="1"),
        )
        log.info("%s started, log %s", script, log_path)
        return proc, log_path

    @staticmethod
    def stop_streamer(proc):
        try:
            proc.stdin.close()
            proc.wait(timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()

    @staticmethod
    def last_line(path):
        lines = [ln for ln in path.read_text(errors="replace").splitlines() if ln.strip()]
        return lines[-1].strip() if lines else ""

    def check_signals(self):
        if self.bridge is not None or self.state != "idle":
            return
        self.banner_note = None
        if not self.use_plux.get():
            preset = self.preset()
            if preset is None or preset["minutes"] is not None:
                messagebox.showwarning("PLUX hub needed", "Rest recordings need the PLUX hub.\n\n"
                                       "Tick \"PLUX hub\" under Devices, then click Check signals.")
                return
            self.state = "checking"
            self.session_on = True
            self.btn_check.config(state="disabled")
            self.btn_disconnect.config(state="normal")
            self.btn_record.config(state="normal")
            self.hub_text = "Hub: not used"
            self.hub_status.config(text=self.hub_text, foreground="black")
            self.rebuild_axes()
            self.ensure_flow()
            return
        self.state = "connecting"
        self.btn_check.config(state="disabled")
        self.btn_disconnect.config(state="normal")
        if self.hub is None:  # never fall back to a hub this PC may not have: find it
            self.find_hub()
        else:
            self.connect_hub()

    def find_hub(self):
        """No hub set on this PC yet: use the only paired biosignalsplux; several: the RA picks."""
        self.set_banner("Looking for the PLUX hub ... (a few seconds)", WARN)
        token = self.finding = object()

        def search():
            hubs, error = find_hubs_now()
            self.root.after(0, lambda: self.on_hubs_found(token, hubs, error))
        threading.Thread(target=search, daemon=True).start()

    def on_hubs_found(self, token, hubs, error):
        if token is not self.finding or self.state != "connecting":
            return  # disconnected meanwhile
        self.finding = None
        if auto_hub(hubs):
            self.set_hub(auto_hub(hubs))
            self.connect_hub()
        elif hubs:
            self.set_banner("Several PLUX hubs found: pick this PC's hub in the window that opened.", WARN)
            HubDialog(self.root, None, self.on_hub_picked, hubs=hubs,
                      on_cancel=lambda: self.back_to_idle("No hub chosen. Click Check signals again."),
                      note="Several PLUX hubs found. Which one is this PC's hub? (MAC on the hub's label)")
        else:
            self.back_to_idle(f"No PLUX hub found ({error or 'none paired with this PC'}). Switch the hub on, "
                              "pair it in Windows Bluetooth settings (or click Change next to Hub and type its "
                              "MAC), then click Check signals again.")

    def on_hub_picked(self, mac):
        self.set_hub(mac)
        if self.state == "connecting" and self.bridge is None:
            self.connect_hub()

    def back_to_idle(self, text):
        if self.state != "connecting":
            return
        self.state = "idle"
        self.btn_check.config(state="normal")
        self.btn_disconnect.config(state="disabled")
        self.set_banner(text, BAD)

    def connect_hub(self):
        ports = format_port_kinds(load_devices()["ports"])  # for sensors without an ID chip
        self.bridge, self.bridge_log = self.launch_streamer("plux_lsl.py", "hub", "--mac", self.hub,
                                                            "--port-kinds", ports)
        self.set_banner(f"Connecting to the hub {self.hub} ... (switch it on if it is off)", WARN)
        bridge = self.bridge

        def wait():
            info = None
            t_end = time.monotonic() + 60  # plux_lsl.py tries 3 times (a just-paired hub may need it)
            while info is None and bridge.poll() is None and time.monotonic() < t_end:
                info = find_plux(timeout=1.0)
            self.root.after(0, lambda: self.on_stream(info))
        threading.Thread(target=wait, daemon=True).start()

    def on_stream(self, info):
        if self.state != "connecting":
            return
        if info is None:
            details = self.bridge_log.read_text(errors="replace")[-600:] if self.bridge_log else ""
            log.warning("hub connection failed: %s", details)
            self.stop_bridge()
            self.back_to_idle(f"Could not connect to the hub {self.hub}.")
            messagebox.showerror("Hub not found", (
                f"Could not connect to the PLUX hub {self.hub}.\n\nCheck that:\n"
                "  - the hub is switched on (light blinking)\n"
                "  - it is charged\n"
                "  - it is within a few metres of the laptop\n"
                "  - OpenSignals is closed (only one program can use the hub)\n"
                "  - it is this PC's hub: the MAC on its label is the one above\n"
                "    (if not, click Change next to Hub)\n\n"
                f"Then click Check signals again.\n\nDetails: {self.bridge_log}"))
            return
        self.monitor = Monitor(info)
        self.rebuild_axes()
        self.state = "checking"
        self.session_on = True
        self.btn_record.config(state="normal")
        text = self.bridge_log.read_text(errors="replace")
        chans = ", ".join(label for label, _ in self.monitor.channels)
        bat, bat_colour = battery_text(text)
        # sensors without an ID chip: plux_lsl.py says which sensor it assumes on which port
        no_chip = re.search(r"(?:No sensor ID chips found|Sensors without ID chip): assuming (.+)", text)
        note = f"  |  sensors have no ID chip, assuming {no_chip.group(1).strip()}" if no_chip else ""
        self.hub_status.config(text=f"Hub: connected, {bat}  |  channels: {chans}  |  {self.monitor.fs} Hz{note}",
                               foreground=bat_colour)
        self.hub_text = self.hub_status.cget("text")
        log.info("stream found: %s, %s%s", chans, bat, note)
        self.ensure_flow()

    def rebuild_axes(self):
        flow_mon = self.flow["monitor"] if self.flow else None
        k = len(self.monitor.channels) if self.monitor else 0
        n = k + (len(flow_mon.channels) if flow_mon else 0)
        self.fig.clear()
        axes = [self.fig.add_subplot(n, 1, i + 1) for i in range(n)]
        for ax in axes:
            ax.grid(alpha=0.3)
        if axes:
            axes[-1].set_xlabel("seconds")
        self.axes, self.flow_axes = axes[:k], axes[k:]
        self.canvas.draw_idle()

    def ensure_flow(self):
        """Start the airflow sensor stream if this recording needs it and it isn't running."""
        if not self.flow_expected() or self.flow is not None:
            return
        proc, log_path = self.launch_streamer("flow_lsl.py", "flow")
        self.flow = {"proc": proc, "log": log_path, "monitor": None, "status": "connecting",
                     "msg": "connecting to the flow sensor ..."}
        flow = self.flow

        def wait():
            info = None
            t_end = time.monotonic() + 20
            while info is None and proc.poll() is None and time.monotonic() < t_end:
                info = find_stream("Flow", timeout=1.0)
            self.root.after(0, lambda: self.on_flow_stream(flow, info))
        threading.Thread(target=wait, daemon=True).start()
        self.update_hub_text()

    def on_flow_stream(self, flow, info):
        if flow is not self.flow:
            return
        if info is None:
            flow["status"] = "failed"
            flow["msg"] = self.last_line(flow["log"]) or "flow sensor not found"
            log.warning("flow sensor failed: %s", flow["msg"])
            self.stop_streamer(flow["proc"])
        else:
            flow["status"], flow["monitor"] = "ok", Monitor(info, kinds={"FLOW"})
            log.info("flow stream found")
            if self.state != "idle":
                self.rebuild_axes()
        self.update_hub_text()

    def stop_flow(self):
        if self.flow is not None:
            self.stop_streamer(self.flow["proc"])
            self.flow = None

    def update_hub_text(self):
        text = getattr(self, "hub_text", self.hub_status.cget("text"))
        if self.flow is not None:
            text += {"connecting": "  |  Flow sensor: connecting ...", "ok": "  |  Flow sensor: connected",
                     "failed": "  |  Flow sensor: NOT CONNECTED"}[self.flow["status"]]
        preset = self.preset()
        if self.state == "checking" and preset and preset["markers"]:
            text += f"  |  {preset['markers']}: " + (
                "running, already recorded (Start manually records it again)" if self.runs.current_recorded()
                else "running" if self.runs.current else "not started yet")
        self.hub_status.config(text=text)

    def flow_statuses(self):
        """Status rows for the flow sensor (empty when it isn't needed)."""
        if not self.flow_expected():
            return []
        if self.flow is None or self.flow["status"] == "connecting":
            return [("Flow", "FLOW", WARN, "connecting to the flow sensor ...")]
        if self.flow["status"] == "failed":
            return [("Flow", "FLOW", BAD, self.flow["msg"] + " - then click Retry flow sensor")]
        mon = self.flow["monitor"]
        if mon.stalled:
            return [("Flow", "FLOW", BAD, "no data from the flow sensor - USB cable / SensorBridge?")]
        return mon.status()

    def retry_flow(self):
        if self.flow is not None and self.flow["status"] == "failed":
            self.flow = None
        self.ensure_flow()

    def stop_bridge(self):
        if self.bridge is None:
            return
        self.stop_streamer(self.bridge)
        self.bridge = None

    def disconnect(self):
        if self.state in ("starting", "recording", "saving"):
            return
        self.stop_bridge()
        self.stop_flow()
        self.finding = None
        self.monitor = None
        self.state = "idle"
        self.session_on = False
        self.btn_check.config(state="normal")
        self.btn_record.config(state="disabled")
        self.btn_disconnect.config(state="disabled")
        self.hub_text = "Hub: not connected"
        self.hub_status.config(text=self.hub_text, foreground="black")
        self.banner_note = self.alert = None
        self.set_banner("Disconnected.", "black")
        self.fig.clear()
        self.canvas.draw_idle()

    # ---------- live loop ----------
    def tick(self):
        try:
            self._tick()
        except Exception:
            self.internal_error("tick failed", sys.exc_info())
        self.root.after(200, self.tick)

    def update_state_line(self):
        """RECORDING sub-X ses-Y since HH:MM (m:ss), N markers, last: <label>; or what the app is doing."""
        info, state = self.rec_info, self.state
        if state == "recording" and info and info["since"]:
            secs = int((datetime.datetime.now() - info["since"]).total_seconds())
            text = (f"RECORDING sub-{info['sub']} ses-{info['ses']} since {info['since']:%H:%M} "
                    f"({secs // 60}:{secs % 60:02d})")
            if info["markers"]:
                text += f", {info['count']} {info['markers']} markers, last: {info['last'] or 'none yet'}"
            elif self.minutes_value:
                text += f" of {self.minutes_value:g} min"
            colour = OK
        elif state == "starting" and self.screen is not None:  # timed window, waiting for Space
            text, colour = "Recorder running - press Space on the participant screen to start", WARN
        elif state == "starting":
            text, colour = "STARTING the recorder ...", WARN
        elif state == "saving":
            text, colour = "SAVING the recording ...", WARN
        elif self.session_on:
            text, colour = "Not recording", "#666"
        else:
            text, colour = "", "#666"
        if self.state_line.cget("text") != text:
            self.state_line.config(text=text, fg=colour)

    def start_marker_count(self, name):
        """Count the task's markers while it is recorded, for the state line, and watch for the
        task's end marker (prefix info["end"]). An inlet is fine now (the recorder is already
        connected); never open one while checking: it would look like a recorder to the task.
        Runs in a thread: it only writes to info; _tick reads info["end_t"] (no Tk calls here)."""
        stop, info = threading.Event(), self.rec_info

        def run():
            inlet = None
            try:
                while not stop.is_set():
                    if inlet is None:
                        found = resolve_byprop("name", name, timeout=1.0)
                        if found:
                            newest = max(found, key=lambda i: i.created_at())
                            info["uid"] = newest.uid()
                            inlet = StreamInlet(newest)
                        continue
                    sample, _ = inlet.pull_sample(timeout=0.5)
                    if sample:
                        info["count"] += 1
                        info["last"] = sample[0]
                        if info["end_t"] is None and is_end_marker(sample[0], info["end"]):
                            info["end_label"] = sample[0]
                            info["end_t"] = time.monotonic()  # set last: _tick checks this one
            except Exception:
                log.exception("marker counter failed")
            finally:
                if inlet is not None:
                    inlet.close_stream()
        threading.Thread(target=run, daemon=True).start()
        self.marker_stop = stop

    def stop_marker_count(self):
        if self.marker_stop is not None:
            self.marker_stop.set()
            self.marker_stop = None

    def _tick(self):
        self.update_state_line()
        if self.bridge is not None and self.bridge.poll() is not None and self.state != "connecting":
            log.error("bridge exited with %s", self.bridge.returncode)
            self.bridge = None
            if self.state == "recording":
                self.finish(f"{self.task}_end_early", lost=True)
            self.disconnect()
            self.set_banner("Lost the hub - check it is on and charged, then Check signals again.", BAD)
            return
        if not self.session_on:
            return
        mon = self.monitor  # None when the hub isn't used
        if mon is not None:
            mon.poll()
        flow = self.flow
        if flow is not None and flow["status"] == "ok":
            if flow["proc"].poll() is not None:
                flow["status"] = "failed"
                flow["msg"] = self.last_line(flow["log"]) or "flow sensor stopped"
                log.error("flow streamer exited: %s", flow["msg"])
                self.update_hub_text()
            else:
                flow["monitor"].poll()
        statuses = mon.status() if mon is not None else []
        flow_rows = self.flow_statuses()
        self.last_statuses = statuses + flow_rows
        self.watch_task()
        if self.state == "recording" and self.check_auto_stop():
            return
        if self.state == "recording":
            if self.end_seq is not None and mon.last_seq >= self.end_seq:
                self.finish(f"{self.task}_end")
            elif mon is not None and time.monotonic() - mon.last_data > LOST_S:
                self.finish(f"{self.task}_end_early", lost=True)
            elif time.monotonic() >= self.next_log:
                self.next_log = time.monotonic() + 60
                log.info("recording: %s, dropped %s", "; ".join(f"{s[0]} {s[3]}" for s in statuses + flow_rows),
                         mon.dropped if mon is not None else "-")
            return  # participant screen is up; skip drawing
        if self.state != "checking":
            return
        if mon is not None and mon.stalled:
            self.set_banner("No data from the hub - is it on and within range?", BAD)
        else:
            bad = [f"{label}: {msg}" for label, _k, colour, msg in statuses + flow_rows if colour != OK]
            if bad:
                self.set_banner("Check:  " + "   |   ".join(bad), WARN)
            elif not statuses + flow_rows:
                self.set_banner("No devices in use: only the task triggers will be recorded.", WARN)
            else:
                self.set_banner("All signals good - ready to record.", OK)
            if self.runs.is_new() and flow is not None \
                    and flow["status"] == "failed":  # task waiting, auto-start held back
                self.set_banner(f"{self.task_name} is waiting for the recorder, but the flow sensor is not "
                                "connected: click Retry flow sensor, or Start manually to record without it.", BAD)
            drawn = False
            if mon is not None and mon.seconds_buffered >= 1:
                draw(self.axes, mon, statuses)
                drawn = True
            if flow is not None and flow["status"] == "ok" and self.flow_axes \
                    and flow["monitor"].seconds_buffered >= 1:
                draw(self.flow_axes, flow["monitor"], flow_rows)
                drawn = True
            if drawn:
                self.canvas.draw_idle()

    # ---------- task trigger watch ----------
    def watch_task(self):
        """Look for the task's trigger stream in every state (a run that appears while a recording
        is starting, running or saving is kept in self.runs). While checking, a run not yet
        recorded starts recording automatically: RAs forget to press Start, and the task waits
        until a recorder receives its triggers. Resolve only - an open inlet here would look like
        a recorder."""
        preset = self.preset()
        name = preset["markers"] if preset else None
        if name != self.task_name:
            self.task_name = name
            if self.runs.seen(None):
                self.update_hub_text()
        if not name:
            self.alert = None
            return
        if not self.task_resolving and time.monotonic() >= self.next_task_resolve:
            self.task_resolving = True
            threading.Thread(target=self.resolve_task, args=(name,), daemon=True).start()
        sub, ses, task, _minutes, problems = self.form_values()
        settled = self.form_settle.update((sub, ses, task), time.monotonic())
        if not self.runs.is_new():
            self.alert = None  # recorded, or the task is gone
        flow_ready = not self.flow_expected() or (self.flow is not None and self.flow["status"] == "ok")
        if not self.runs.should_auto_start(self.state, flow_ready):
            return  # each run once; wait for the flow sensor so it is in the recording (banner explains)
        # A run that cannot start stays new (not marked recorded): it starts once the cause is fixed.
        held = ("fill in the form (" + "; ".join(problems) + ")" if problems
                else "no data from the hub" if self.monitor is not None and self.monitor.stalled else None)
        if held:
            if self.auto_blocked != self.runs.current:
                self.auto_blocked = self.runs.current
                log.error("%s started but cannot auto-start: %s", name, held)
                self.root.bell()
            self.alert = f"{name} started - NOT RECORDING: {held}. Recording then starts by itself."
            return
        if not settled:
            return  # just typed: wait until participant/session stay the same for FORM_SETTLE_S
        self.alert = None
        log.warning("%s stream appeared while not recording: starting automatically", name)
        self.root.bell()
        self.start_recording(auto=f"{name} started")

    def resolve_task(self, name):
        """Background: find the newest stream called `name` (a restarted task has a new uid)."""
        found = resolve_byprop("name", name, timeout=1.0)
        uid = max(found, key=lambda i: i.created_at()).uid() if found else None
        self.root.after(0, lambda: self.on_task_resolved(name, uid))

    def on_task_resolved(self, name, uid):
        self.task_resolving = False
        self.next_task_resolve = time.monotonic() + TASK_RESOLVE_S
        if name != self.task_name:
            return
        if self.runs.seen(uid):
            log.info("task trigger stream %s (%s, %s)", "found" if uid else "gone", self.state,
                     "already recorded" if self.runs.current_recorded() else "new run" if uid else "-")
            self.update_hub_text()
        if self.auto_stop is not None and self.state == "recording" and self.rec_info \
                and name == self.rec_info["markers"]:
            self.auto_stop.resolved(self.runs.current_recorded(), time.monotonic())

    def check_auto_stop(self):
        """While a task is recorded: stop by itself END_GRACE_S after the task's end marker, or when
        the recorded run's trigger stream has been gone for a while (task crashed or closed).
        Returns True if it stopped. The Stop button stays as a manual override."""
        stopper, info = self.auto_stop, self.rec_info
        if stopper is None or info is None:
            return False
        if info["end_t"] is not None and stopper.end_t is None:
            stopper.ended(info["end_label"], info["end_t"])
            log.info("%s end marker %r: stopping in %s s", info["markers"], info["end_label"], END_GRACE_S)
            self.set_banner(f"{info['markers']} ended ({info['end_label']}): the recording stops by itself "
                            f"in {END_GRACE_S} s ...", OK)
        reason = stopper.reason(time.monotonic())
        if reason is None:
            return False
        text = f"Recording stopped AUTOMATICALLY: {info['markers']} {reason}"
        log.info(text)
        if stopper.end_t is not None and info.get("uid"):
            self.runs.mark_recorded(info["uid"])  # the run that ended is not auto-started again
        self.banner_note = (text, OK if stopper.end_t is not None else WARN)
        self.finish(f"{self.task}_end")
        return True

    def mark_recorded(self, name):
        """Background, at recording start: every run of the task online now is in this recording."""
        uids = [info.uid() for info in resolve_byprop("name", name, minimum=10, timeout=1.0)]
        self.root.after(0, lambda: self.runs.mark_recorded(*uids))

    def set_banner(self, text, colour):
        """The routine banner, below any sticky lines (self.alert, self.banner_note); the colour is
        the most urgent of them."""
        for note, note_colour in [self.banner_note or (None, None), (self.alert, BAD)]:
            if note:
                text = f"{note}\n{text}"
                if note_colour == BAD or (note_colour == WARN and colour != BAD):
                    colour = note_colour
        if time.monotonic() < self.error_until:  # an internal error stays on top for a while
            text, colour = f"{self.error_text}\n{text}", BAD
        self.banner.config(text=text, fg=colour)

    # ---------- errors: never fail silently (pythonw has no console) ----------
    def internal_error(self, what, exc_info):
        """Log an unexpected exception (once per kind per minute) and say so on the banner."""
        key = (exc_info[0], str(exc_info[1]))
        now = time.monotonic()
        if key != self.last_error or now - self.last_error_t > 60:
            log.error("%s", what, exc_info=exc_info)
            self.last_error, self.last_error_t = key, now
        self.error_text = f"Internal error: see logs\\{LOG_FILE.name}"
        self.error_until = now + 60
        self.set_banner(self.banner.cget("text").split("\n")[-1], BAD)

    def tk_error(self, exc, val, tb):
        """root.report_callback_exception: a button or after() callback raised."""
        self.internal_error("Tk callback failed", (exc, val, tb))

    def thread_error(self, args):
        """threading.excepthook: a background thread raised."""
        if issubclass(args.exc_type, SystemExit):
            return
        name = args.thread.name if args.thread else "?"
        exc_info = (args.exc_type, args.exc_value, args.exc_traceback)
        try:
            self.root.after(0, lambda: self.internal_error(f"thread {name} failed", exc_info))
        except RuntimeError:  # Tk already gone
            log.error("thread %s failed", name, exc_info=exc_info)

    # ---------- recording ----------
    def start_recording(self, auto=None):
        """auto: reason text when started automatically (task began unrecorded) - no dialogs then."""
        if self.state != "checking":
            return
        sub, ses, task, minutes, problems = self.form_values()
        if problems:
            if auto:  # watch_task holds an auto-start back for this; no dialog over the task
                log.error("%s but cannot auto-start: %s", auto, problems)
                return
            messagebox.showwarning("Check the form", "\n".join(problems))
            return
        statuses = getattr(self, "last_statuses", [])
        bad = [f"  {label}: {msg}" for label, _k, colour, msg in statuses if colour != OK]
        if minutes is not None and self.monitor is None:  # a timed window is counted in hub samples
            messagebox.showwarning("PLUX hub needed", "Timed recordings need the PLUX hub.\n\n"
                                   "Disconnect, tick \"PLUX hub\" under Devices, then Check signals.")
            return
        if self.monitor is not None and self.monitor.stalled:
            if auto:
                log.error("%s but the hub sends no data", auto)
                self.set_banner(f"{auto} - NOT RECORDING: no data from the hub.", BAD)
                return
            messagebox.showerror("No data", "No data from the hub. Fix the connection first.")
            return
        previous = previous_recording(DATA_DIR, sub, ses, task)
        repeat = already_recorded_text(sub, ses, task, previous) if previous is not None else None
        if repeat and auto:  # the task is already running: record (a new _run-N file) and warn
            log.warning("%s: auto-start records it again as a new file", repeat)
        elif repeat:
            if not messagebox.askyesno("Already recorded", f"{repeat}. Record again as a new file?",
                                       icon="warning", default="no"):
                log.info("%s: RA chose not to record again", repeat)
                return
            log.info("%s: RA chose to record again as a new file", repeat)
        if bad and not auto and not messagebox.askyesno(
                "Signals not all good", "These signals do not look good:\n\n" + "\n".join(bad) +
                "\n\nFix them first if you can. Record anyway?", icon="warning", default="no"):
            return
        preset = self.preset()
        needed = preset["markers"] if preset else None
        self.runs.mark_recorded(self.runs.current)
        if needed and not auto and not resolve_byprop("name", needed, timeout=2):
            if not messagebox.askyesno(
                    "Task triggers not found",
                    f"The {needed} task's trigger stream was not found.\n\n"
                    f"Start {needed} first (up to its first screen): recording then starts by itself "
                    "and its triggers are recorded.\n\nRecord without task triggers anyway?",
                    icon="warning", default="no"):
                return
        self.task, self.minutes_value = task, minutes
        self.expect_flow = self.flow_expected()
        self.expect_plux = self.monitor is not None
        self.path = output_path(DATA_DIR, sub, ses, task)
        self.rec_log = rec_log = labrecorder_log_path(self.path)
        self.rec_info = {"sub": sub, "ses": ses, "since": None, "markers": needed if minutes is None else None,
                         "count": 0, "last": None, "uid": None,
                         "end": preset.get("end") if preset and minutes is None else None,
                         "end_label": None, "end_t": None}
        self.banner_note = ((f"REPEAT: {repeat}. Recording it again as a new file ({self.path.name}).", WARN)
                            if repeat and auto else None)
        self.alert = None
        self.clear_summary(note=f"Recording {self.path.name} ...")
        self.state = "starting"
        for b in (self.btn_record, self.btn_disconnect, self.btn_check):
            b.config(state="disabled")
        self.set_form_enabled(False)
        self.set_banner(f"Starting recorder ... ({self.path.name})", WARN)
        self.auto_reason = auto
        log.info("recording %s, %s min, signals: %s%s", self.path, minutes or "until stop", bad or "all good",
                 f" (auto: {auto})" if auto else "")

        def launch():
            proc, names = start_recorder(self.path, console=False,
                                         optional=(("Flow",) if self.expect_flow else ())
                                         + EXTRA_STREAMS.get(task, ()),
                                         required=("PLUX",) if self.expect_plux else (),
                                         log_path=rec_log)
            t0 = time.monotonic()
            if needed:
                self.mark_recorded(needed)
            time.sleep(max(0.0, LEAD_IN_S - (time.monotonic() - t0)))
            self.root.after(0, lambda: self.on_recorder_ready(proc, names))
        threading.Thread(target=launch, daemon=True).start()

    def on_recorder_ready(self, proc, names):
        self.recorder = proc
        if proc.poll() is not None:
            self.state = "checking"
            self.btn_record.config(state="normal")
            self.btn_disconnect.config(state="normal")
            self.set_form_enabled(True)
            messagebox.showerror("Recorder failed", f"The recorder did not start.\n\nDetails: "
                                 f"{self.rec_log}")
            return
        log.info("recorder streams: %s", names)
        if self.minutes_value is None:  # task: the task owns the screen
            self.begin_window()
            self.btn_record.config(text="Stop recording", command=self.stop_task, state="normal")
            # This window stays where it is (the task runs on the participant screen): hiding it
            # looked like a crash. The state line above shows the recording is alive.
            stops = ("It stops by itself when the task ends (click Stop recording if it doesn't)."
                     if self.auto_stop is not None else "Click Stop recording when the task ends.")
            if self.auto_reason:
                self.set_banner(f"Recording started AUTOMATICALLY: {self.auto_reason}. {stops} "
                                f"({', '.join(names)})", OK)
                return
            self.set_banner(f"Recording ({', '.join(names)}). Run the task now. {stops}", OK)
            return
        self.set_banner("Participant screen is up. Press Space on it to start the window "
                        "(Esc twice, then Y, stops early).", WARN)
        self.screen = ParticipantScreen(self.root, self.begin_window, self.abort_window)

    def begin_window(self):
        self.markers.push_sample([f"{self.task}_start"], local_clock())
        self.start_seq = self.monitor.last_seq if self.monitor is not None else None
        self.end_seq = (self.start_seq + int(self.minutes_value * 60 * self.monitor.fs)
                        if self.minutes_value else None)
        self.state = "recording"
        self.next_log = time.monotonic() + 60
        log.info("%s_start at nSeq %s", self.task, self.start_seq)
        if self.rec_info is not None:
            self.rec_info["since"] = datetime.datetime.now()
            self.auto_stop = AutoStop() if self.rec_info["markers"] and self.rec_info["end"] else None
            if self.rec_info["markers"]:
                self.start_marker_count(self.rec_info["markers"])

    def stop_task(self):
        if messagebox.askyesno("Stop recording", "Has the task finished? Stop the recording now?"):
            self.finish(f"{self.task}_end")

    def abort_window(self):
        if self.state == "recording":
            self.finish(f"{self.task}_end_early")
        elif self.state == "starting":  # Esc before Space: nothing worth keeping
            log.info("aborted before start; removing %s", self.path)
            self.screen.destroy()
            self.screen = None
            proc, path, rec_log = self.recorder, self.path, self.rec_log
            self.state = "saving"

            def cleanup():
                stop_recorder(proc)
                for p in (path, rec_log):
                    p.unlink(missing_ok=True)
                self.root.after(0, self.back_to_checking)
            threading.Thread(target=cleanup, daemon=True).start()

    def finish(self, label, lost=False):
        if self.state != "recording":
            return
        self.markers.push_sample([label], local_clock())
        info = self.rec_info or {}
        log.info("%s (lost=%s)%s", label, lost,
                 f", {info['count']} {info['markers']} markers seen, last {info['last']}" if info.get("markers")
                 else "")
        self.stop_marker_count()
        self.auto_stop = None
        self.state = "saving"
        if self.screen is not None:
            self.screen.finished()
            self.screen = None
        if lost:
            messagebox.showerror("Connection lost", "Lost data from the hub during the recording.\n"
                                 "The data up to that point is saved. Check the hub, then record again.")
        self.set_banner("Saving and checking the recording ...", WARN)
        proc, path = self.recorder, self.path

        def save():
            stop_recorder(proc)
            try:
                report = assess(path, expect=("PLUX",) * self.expect_plux + ("Flow",) * self.expect_flow)
            except Exception as exc:  # unreadable file
                log.exception("assess failed")
                report = {"ok": False, "lines": [(f"Could not read the file: {exc}", False)]}
            self.root.after(0, lambda: self.show_summary(path, report))
        threading.Thread(target=save, daemon=True).start()

    def show_summary(self, path, report):
        self.clear_summary(note=None)
        verdict = "PASS" if report["ok"] else "CHECK"
        tk.Label(self.summary, text=verdict, font=("Segoe UI", 28, "bold"),
                 fg=OK if report["ok"] else WARN).pack(anchor="w", pady=(6, 0))
        tk.Label(self.summary, text=path.name, font=FONT, fg="#444", wraplength=self.summary_wrap,
                 justify="left").pack(anchor="w", pady=(0, 8))
        for text, good in report["lines"]:
            tk.Label(self.summary, text=("! " if good is False else "") + text, font=FONT,
                     fg=BAD if good is False else "black", wraplength=self.summary_wrap,
                     justify="left").pack(anchor="w", pady=1)
        ttk.Button(self.summary, text="Open data folder",
                   command=lambda: os.startfile(path.parent)).pack(anchor="w", pady=10)
        log.info("summary %s: %s", verdict, report["lines"])
        if self.task in FIGURES and (ROOT / FIGURES[self.task]).exists():
            self.make_figures(FIGURES[self.task], path)
        self.back_to_checking()

    def make_figures(self, script, path):
        """Run the task's figure script in the background; a button opens the figures when done."""
        note = tk.Label(self.summary, text="Making figures ...", font=FONT, fg="#666")
        note.pack(anchor="w")
        log_path = LOG_DIR / f"figures_{path.stem}.log"

        def run():
            with open(log_path, "w") as fh:
                code = subprocess.run([str(ROOT / ".venv" / "Scripts" / "python.exe"), str(ROOT / script),
                                       str(path)], stdout=fh, stderr=subprocess.STDOUT, cwd=ROOT,
                                      creationflags=subprocess.CREATE_NO_WINDOW).returncode
            self.root.after(0, lambda: done(code))

        def done(code):
            if not note.winfo_exists():  # a newer recording replaced the summary
                return
            out_dir = path.parent / f"{path.stem}_figures"
            if code == 0 and out_dir.exists():
                log.info("figures in %s", out_dir)
                note.destroy()
                ttk.Button(self.summary, text="Open figures",
                           command=lambda: os.startfile(out_dir)).pack(anchor="w")
            else:
                log.error("figure script failed (%s), see %s", code, log_path)
                note.config(text=f"Figures failed (the recording is fine). Details: {log_path.name}")
        threading.Thread(target=run, daemon=True).start()

    def clear_summary(self, note="Nothing recorded yet."):
        for w in self.summary.winfo_children():
            w.destroy()
        if note:
            tk.Label(self.summary, text=note, font=FONT, fg="#666", wraplength=self.summary_wrap,
                     justify="left").pack(anchor="w", pady=6)

    def back_to_checking(self):
        self.recorder = None
        self.set_form_enabled(True)
        self.btn_record.config(text=self.start_text(), command=self.start_recording)
        self.root.deiconify()
        if self.session_on:
            self.state = "checking"
            self.btn_record.config(state="normal")
            self.btn_disconnect.config(state="normal")
        else:
            self.state = "idle"
            self.btn_check.config(state="normal")

    def on_close(self):
        if self.state in ("starting", "recording", "saving"):
            if not messagebox.askyesno("Recording in progress", "A recording is running. Stop it and quit?",
                                       icon="warning", default="no"):
                return
            if self.state == "recording":
                self.markers.push_sample([f"{self.task}_end_early"], local_clock())
            if self.recorder is not None:
                stop_recorder(self.recorder)
        self.stop_marker_count()
        self.stop_bridge()
        self.stop_flow()
        self.root.destroy()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sub")
    ap.add_argument("--ses")
    ap.add_argument("--recording", help="preset name or a label such as restbaseline")
    ap.add_argument("--minutes", type=float)
    args = ap.parse_args()

    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    try:
        root = tk.Tk()
        app = App(root, args)
        root.report_callback_exception = app.tk_error
        threading.excepthook = app.thread_error
        root.mainloop()
    except Exception:
        log.exception("Physio Recorder failed")
        raise


if __name__ == "__main__":
    sys.exit(main())
