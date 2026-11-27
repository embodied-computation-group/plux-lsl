"""File naming and LabRecorderCLI control shared by record.py and physio_app.py."""

import datetime
import re
import subprocess
import time
from pathlib import Path

import lsl_quiet  # noqa: F401  (must precede pylsl)
from pylsl import resolve_streams

ROOT = Path(__file__).resolve().parent
LABRECORDER_CLI = ROOT / "LabRecorder" / "LabRecorderCLI.exe"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"


def output_path(out_dir, sub, ses, task):
    """data/sub-XX/ses-Y/sub-XX_ses-Y_task-T_physio.xdf; never an existing file (adds _run-N)."""
    folder = Path(out_dir) / f"sub-{sub}" / f"ses-{ses}"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"sub-{sub}_ses-{ses}_task-{task}"
    path = folder / f"{stem}_physio.xdf"
    run = 2
    while path.exists():
        path = folder / f"{stem}_run-{run}_physio.xdf"
        run += 1
    return path


def log_stamp(now=None):
    """YYYYMMDD-HHMMSS (now, or the given datetime): log names never repeat across starts."""
    return (now or datetime.datetime.now()).strftime("%Y%m%d-%H%M%S")


def app_log_path(now=None, log_dir=LOG_DIR):
    """logs/app_YYYYMMDD-HHMMSS.log: one file per app start."""
    return Path(log_dir) / f"app_{log_stamp(now)}.log"


def labrecorder_log_path(xdf_path, now=None):
    """<xdf stem>_YYYYMMDD-HHMMSS.labrecorder.log next to the recording."""
    path = Path(xdf_path)
    return path.with_name(f"{path.stem}_{log_stamp(now)}.labrecorder.log")


def previous_recording(out_dir, sub, ses, task):
    """The newest recording of sub/ses/task already in out_dir (the plain file or a _run-N one),
    by modification time; None if there is none. Creates no folders."""
    folder = Path(out_dir) / f"sub-{sub}" / f"ses-{ses}"
    stem = f"sub-{sub}_ses-{ses}_task-{task}"
    same = re.compile(rf"{re.escape(stem)}(_run-\d+)?_physio\.xdf")
    found = [p for p in folder.glob(f"{stem}*_physio.xdf") if same.fullmatch(p.name)]
    return max(found, key=lambda p: p.stat().st_mtime, default=None)


def already_recorded_text(sub, ses, task, path, today=None):
    """'sub-X ses-Y <task> was already recorded at HH:MM (<file name>)'; the date is added when the
    file is not from today."""
    when = datetime.datetime.fromtimestamp(Path(path).stat().st_mtime)
    at = f"{when:%H:%M}" if when.date() == (today or datetime.date.today()) else f"{when:%Y-%m-%d %H:%M}"
    return f"sub-{sub} ses-{ses} {task} was already recorded at {at} ({Path(path).name})"


def start_recorder(path, console=True, optional=("Flow",), required=("PLUX",), log_path=None):
    """Launch LabRecorderCLI on the `required` streams, SessionMarkers, any task marker
    streams online, and those `optional` streams (e.g. Flow) that are online. Its output goes
    to log_path (default: labrecorder_log_path(path)).

    Returns (process, names of the streams being recorded).
    """
    online = resolve_streams(1.0)
    others = sorted({s.name() for s in online if s.type() == "Markers" and s.name() != "SessionMarkers"})
    extra = sorted({s.name() for s in online if s.name() in optional and s.name() not in required})
    names = [*required, *extra, "SessionMarkers", *others]
    log = open(log_path or labrecorder_log_path(path), "w")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP  # keep Ctrl+C away from it
    if not console:
        flags |= subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(
        [str(LABRECORDER_CLI), str(path), *[f"name='{n}'" for n in names]],
        stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
    )
    return proc, names


def stop_recorder(proc):
    """Let the last chunks arrive, then ask LabRecorderCLI to finish the file."""
    if proc.poll() is not None:
        return
    time.sleep(1.5)
    try:
        proc.communicate(b"\n", timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
