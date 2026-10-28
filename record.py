"""Record PLUX biosignals to XDF, e.g. a resting HRV / breathing window.

Usage (from the plux-lsl folder):
    .venv\\Scripts\\python.exe record.py --sub 01 --ses 1 --minutes 5
    .venv\\Scripts\\python.exe record.py --sub 01 --ses 1 --task rrst      # until Ctrl+C; records task markers too

1. Connects to the hub and opens the live signal check window.
2. Press Enter in this console once every channel title is green.
3. Records the window, then prints a quality summary.

Writes data\\sub-<sub>\\ses-<ses>\\sub-<sub>_ses-<ses>_task-<task>_physio.xdf
(never overwrites: a second run gets _run-2, and so on). The file holds the
PLUX stream, a SessionMarkers stream (<task>_start / <task>_end) and any other
LSL marker streams that are online when recording starts.
"""

import argparse
import msvcrt
import signal
import subprocess
import sys
import threading

import lsl_quiet  # noqa: F401  (must precede pylsl)
from pylsl import StreamInfo, StreamOutlet, cf_string, local_clock

import plux_lsl
from check_xdf import summarize
from recorder import DATA_DIR, ROOT, output_path, start_recorder, stop_recorder

LEAD_IN_S = 3  # recorder warm-up before <task>_start; also the data kept either side of the window


def launch_recorder(path):
    proc, names = start_recorder(path)
    print(f"Recording streams: {', '.join(names)}", flush=True)
    return proc


def enter_pressed():
    while msvcrt.kbhit():
        if msvcrt.getwch() in "\r\n":
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sub", required=True, help="participant label, e.g. 01")
    ap.add_argument("--ses", required=True, help="session label, e.g. 1")
    ap.add_argument("--task", default="rest", help="task label (default rest)")
    ap.add_argument("--minutes", type=float, help="length of the window; omit to record until Ctrl+C")
    ap.add_argument("--out", default=str(DATA_DIR), help="data root folder (default plux-lsl\\data)")
    ap.add_argument("--no-viewer", action="store_true", help="don't open the signal check window")
    ap.add_argument("--start-now", action="store_true", help="skip the Enter prompt and record straight away")
    plux_lsl.add_device_args(ap)
    args = ap.parse_args()

    path = output_path(args.out, args.sub, args.ses, args.task)
    hub, sources, channels, properties = plux_lsl.connect(args)
    rate = args.rate
    markers = StreamOutlet(StreamInfo("SessionMarkers", "Markers", 1, 0, cf_string, "plux-lsl-session"))
    hub.outlet = plux_lsl.make_outlet(args.mac, rate, args.bits, channels, properties)
    viewer = None
    if not args.no_viewer:
        viewer = subprocess.Popen([sys.executable, str(ROOT / "viewer.py")])

    # phase: setup -> lead_in -> recording -> lead_out -> done
    st = {"phase": "setup", "recorder": None, "start": None, "end": None,
          "stop_at": None, "next_report": None, "early": False}
    window = int(args.minutes * 60 * rate) if args.minutes else None

    def begin(nseq):
        # Starting the recorder takes over a second; doing that inside the data
        # callback stalls the hub, so it runs in a thread and lead_in waits for it.
        st["phase"] = "launching"
        threading.Thread(target=lambda: st.update(recorder=launch_recorder(path)), daemon=True).start()

    def finish(nseq, label):
        markers.push_sample([label], local_clock())
        print(label, flush=True)
        st["phase"] = "lead_out"
        st["stop_at"] = nseq + LEAD_IN_S * rate

    def on_sample(nseq):
        phase = st["phase"]
        if phase == "setup":
            if args.start_now and nseq == 0:
                begin(nseq)
            elif nseq % 50 == 0 and enter_pressed():
                begin(nseq)
            elif hub.stop_requested:
                return True
        elif phase == "launching":
            if st["recorder"] is not None:
                st["start"] = nseq + LEAD_IN_S * rate
                st["phase"] = "lead_in"
                length = f"{args.minutes:g} min" if args.minutes else "until Ctrl+C"
                print(f"Recording {length} to {path}", flush=True)
            elif hub.stop_requested:
                return True
        elif phase == "lead_in":
            if st["recorder"].poll() is not None:
                print("LabRecorderCLI exited early; see the .labrecorder.log next to the data file")
                return True
            if nseq >= st["start"]:
                markers.push_sample([f"{args.task}_start"], local_clock())
                print(f"{args.task}_start", flush=True)
                st["phase"] = "recording"
                st["end"] = st["start"] + window if window else None
                st["next_report"] = st["start"] + 30 * rate
        elif phase == "recording":
            if st["end"] is not None and nseq >= st["end"]:
                finish(nseq, f"{args.task}_end")
            elif hub.stop_requested:
                st["early"] = True
                finish(nseq, f"{args.task}_end_early" if window else f"{args.task}_end")
            elif nseq >= st["next_report"]:
                done = (nseq - st["start"]) / rate / 60
                left = f", {(st['end'] - nseq) / rate / 60:.1f} min left" if st["end"] else ""
                print(f"  {done:5.1f} min recorded{left}  (dropped samples: {hub.n_dropped})", flush=True)
                st["next_report"] += 30 * rate
        elif phase == "lead_out":
            return nseq >= st["stop_at"]
        return False

    hub.on_sample = on_sample
    hub.quiet = True
    signal.signal(signal.SIGINT, lambda *_: setattr(hub, "stop_requested", True))
    if not args.start_now:
        print("\nCheck the signal window: every channel title should be green.\n"
              "Press Enter here to start recording (Ctrl+C to quit without recording).", flush=True)
    try:
        hub.run(rate, sources)
    finally:
        recorder = st["recorder"]
        if recorder is not None:
            stop_recorder(recorder)
        hub.close()
        if viewer is not None and viewer.poll() is None:
            viewer.terminate()

    if recorder is None:
        print("Nothing recorded.")
        return
    print(f"\nSaved {path}")
    summarize(path)


if __name__ == "__main__":
    main()
