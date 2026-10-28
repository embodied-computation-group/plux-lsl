# Physio Recorder (PLUX + flow, LSL)

Records PLUX biosignals (ECG, respiration belt) and, for RRST, the airflow sensor, together
with task triggers, into one `.xdf` file per recording. No OpenSignals needed.

Data: `plux-lsl\data\sub-<ID>\ses-<N>\sub-<ID>_ses-<N>_task-<recording>_physio.xdf`
(a repeat never overwrites; it gets `_run-2`, `_run-3`, ...). Logs for troubleshooting: `plux-lsl\logs\`.

---

## RA checklist: resting recording (15 min, baseline or peak)

1. **Hub on.** Press the button on the biosignalsplux hub; its light blinks. Battery shows in the app.
2. **Sensors on the participant.** Respiration belt around the chest, snug but comfortable. ECG electrodes
   on clean, dry skin, with the leads clipped on.
3. Open **Physio Recorder** (desktop shortcut).
4. Fill in **Participant** and **Session**, and pick **Rest - baseline** or **Rest - peak**
   (15 min is filled in).
5. Click **1 Check signals**. Wait about 15 s.
   - Every channel title should turn **green** (e.g. `ECG2 HR 68 bpm`, `RESP1 14 breaths/min`).
   - Amber or red means fix it first. See *What the warnings mean* below.
6. Click **2 Start recording**. The screen turns grey with instructions for the participant.
7. Read or explain the instructions, then **press Space**. A fixation cross shows for 15 minutes.
   - To stop early: press **Esc twice**, then **Y**.
8. When it says *Finished*, check the **Last recording** panel on the right:
   - **PASS**: done.
   - **CHECK**: lines marked `!` say what went wrong. Note it in the session log and tell the researcher.

## RA checklist: RRST (with flow sensor)

1. Plug in the **RRST device**, the **SensorBridge** (flow sensor) and switch on the **PLUX hub**.
2. Sensors on the participant (belt, ECG) as above. Mouthpiece and circuit connected, with the
   flow sensor arrow **pointing toward the participant**.
3. Open **Physio Recorder**, fill in Participant/Session, pick **RRST**.
4. Click **1 Check signals**. The flow sensor connects automatically ("Flow sensor: connected" in the
   status line). Ask the participant for **one or two test breaths through the mouthpiece**:
   the Flow trace should show the breaths and its title should go green.
5. **Start RRST** in MATLAB, from `Desktop\cannabodies\rrst-toolkit`:
   `cfg = pilotConfig(); cfg.device.port = "COM7"; runRRST(cfg)`.
   **Recording starts by itself** as soon as RRST is running (the app beeps and says
   *Recording started AUTOMATICALLY*); RRST waits until the recorder receives its triggers.
   Run the task as normal.
   - If it doesn't start (red banner, e.g. flow sensor not connected): fix it, or click
     **2 Start recording** to record without it.
6. When RRST has finished, click **Stop recording** in Physio Recorder, then confirm.
7. Check the **Last recording** panel: PASS / CHECK. `RRST triggers: N (k onsets)`: each trial sends
   about 9 triggers, 2 of them onsets (one per breath). The pilot's 100 trials give about 900 triggers
   and 200 onsets, plus a few more for repeated trials. A shorter or aborted run gives fewer.

## Devices on this PC

The **Devices** tick boxes say which devices this PC has: **PLUX hub** and **Flow sensor**. They are
remembered (in `devices.json`) and can be changed only while disconnected. An unticked device is not
started, not waited for, and not expected in the summary. RRST then records with whatever is ticked:
hub + flow, hub only, flow only, or task triggers only. Rest recordings need the PLUX hub.

## What the warnings mean

| Message | What to do |
|---|---|
| `Could not connect to the hub` | Hub switched on? Charged? Within a few metres? OpenSignals closed? Then *Check signals* again. |
| `NO DATA` / `Lost the hub` | Hub turned off, out of range or battery empty. Recording up to that point is saved. |
| `FLAT - sensor not connected or not on the body` | Sensor cable not plugged into the hub, or sensor not on the participant. |
| `CLIPPING` | Signal too large: check placement, ask the participant to keep still. |
| `50 Hz mains noise - check electrodes...` | ECG electrodes not on skin, dried out, or leads unclipped. Replace electrodes / re-clip. |
| `no clear heartbeat - check electrodes and lead colours` | Electrodes on but no clean beat: check positions and lead colours. |
| `no clear breathing - check belt tension / position` | Belt too loose or too high/low. Tighten slightly, move to the lower chest. |
| Flow: `no breaths through the circuit` | Normal between RRST trials. During setup: mouthpiece in? Circuit sealed? |
| Flow: `REVERSE FLOW` | Flow sensor mounted backwards: arrow must point toward the participant. |
| Flow: `CONSTANT FLOW` | Air moving all the time: leak in the circuit or a draught. |
| Flow: `NOT CONNECTED` / `SensorBridge not found` | USB cable, or close SEK-ControlCenter (only one program can use the bridge). Then *Retry flow sensor*. |
| `Task triggers not found` (RRST) | RRST isn't running: start it in MATLAB; recording then starts by itself. |
| `RRST is waiting for the recorder, but the flow sensor is not connected` | *Retry flow sensor* (USB cable, SEK-ControlCenter closed); recording then starts by itself. Or click Start recording to record without flow. |
| `Dropped samples` > 0 | Bluetooth dropouts: keep the hub close to the laptop and charged. |

## For researchers

- `record.py`: command-line recorder (`--sub 01 --ses 1 --minutes 5`, or `--task rrst` to record until Ctrl+C).
- `check_xdf.py <file>`: the PASS/CHECK summary for any recording.
- `epoch.py <file> --stream Flow --events "interval\d_onset_" --pre 1 --post 3 --groups load null`:
  per-trial epochs + averages (RRST markers: see the rrst-toolkit README, "Event markers (LSL)").
- `segments.py <file> --start "level_(\w+)_ready" --end "level_{}_done"`: variable-length segments
  (wedge-level bench test).
- `plux_lsl.py --list` / `flow_lsl.py --list`: check that a device connects; prints the sensors detected.
- Streams: `PLUX` (raw ADC counts, int32; column 0 `nSeq`), `Flow` (slm, degC; column 0 `nSeq`),
  `SessionMarkers` (`<task>_start` / `<task>_end` / `<task>_end_early`), task markers (e.g. `RRST`).
- Software: Python 3.13 venv in `.venv` (PLUX's API has no 3.11 build), LabRecorder 1.18 in `LabRecorder\`,
  PLUX API binaries in `vendor\plux\`.

## Setting up a new PC

1. Install Python 3.13 (64-bit) for the current user. It doesn't need to be on PATH.
2. From this folder: `py -3.13 -m venv .venv`, then `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
3. Download LabRecorder 1.18.0 for Windows (github.com/labstreaminglayer/App-LabRecorder, releases)
   and unzip it into `LabRecorder\`, so that `LabRecorder\LabRecorderCLI.exe` exists.
4. Pair the PLUX hub in Windows Bluetooth settings. Then check it: `.venv\Scripts\python.exe plux_lsl.py --list`.
5. Open the app and set **Devices** for this PC.

Recordings in `data\` stay out of git, except the researchers' own pilot recordings listed in `.gitignore`.
