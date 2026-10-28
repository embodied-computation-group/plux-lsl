"""File naming and LabRecorderCLI control shared by record.py and physio_app.py."""

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


def start_recorder(path, console=True, optional=("Flow",), required=("PLUX",)):
    """Launch LabRecorderCLI on the `required` streams, SessionMarkers, any task marker
    streams online, and those `optional` streams (e.g. Flow) that are online.

    Returns (process, names of the streams being recorded).
    """
    online = resolve_streams(1.0)
    others = sorted({s.name() for s in online if s.type() == "Markers" and s.name() != "SessionMarkers"})
    extra = sorted({s.name() for s in online if s.name() in optional and s.name() not in required})
    names = [*required, *extra, "SessionMarkers", *others]
    log = open(Path(path).with_suffix(".labrecorder.log"), "w")
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
