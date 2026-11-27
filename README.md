# Physio Recorder (PLUX + flow, LSL)

Records PLUX biosignals (ECG, respiration belt) and, for RRST, the airflow sensor, together
with task triggers, into one `.xdf` file per recording. No OpenSignals needed.

Data: `plux-lsl\data\sub-<ID>\ses-<N>\sub-<ID>_ses-<N>_task-<recording>_physio.xdf`
(a repeat never overwrites; it gets `_run-2`, `_run-3`, ...). Logs for troubleshooting: `plux-lsl\logs\`.

---

## RA checklist: resting recording (15 min, baseline or peak)

1. **Hub on.** Press the button on the biosignalsplux hub; its light blinks. Battery shows in the app.
2. **Sensors on the participant.** Respiration belt around the chest, snug but comfortable. ECG electrodes
   on clean, dry skin, with the leads clipped on. Cables into the hub: **belt in port 1, ECG in port 2**.
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

**Hub: 00:07:80:xx:xx:xx [Change]** next to the tick boxes is this PC's own PLUX hub (each site has its
own). Its MAC is printed on the hub's label.
- The first time, it says *not set yet*: **Check signals** finds the hub paired with this PC and keeps it.
  If several hubs are paired, a window asks which one; pick the one whose MAC is on your hub's label.
- To use another hub: **Disconnect**, click **Change**, pick it from the list or type its MAC
  (six pairs such as `00:07:80:4D:33:82`), then **OK**.
- A new hub must first be paired in **Windows Settings > Bluetooth & devices > Add device** (hub switched
  on). Right after pairing the hub may not be found yet: wait a minute, then *Check signals* again.

**Sensor cables:** belt in **port 1**, ECG in **port 2** of the hub (the lab's standard wiring). Sensors
with an ID chip are recognised on any port. Sensors **without** an ID chip are not: the app then assumes
the standard wiring, and the hub line says *sensors have no ID chip, assuming breathing belt (RESP) on
port 1, ECG on port 2*. If a channel then says `FLAT`, check the cable is in the right port. A site that
wires differently: the researcher sets `"ports"` in `devices.json`, e.g. `"ports": {"1": "RESP", "2": "ECG"}`.

## What the window shows

- The line under the hub status says what is happening, at a glance:
  `RECORDING sub-01 ses-1 since 10:39 (4:12), 57 RRST markers, last: trial_5_go` while a task is recorded
  (the time ticks; the marker count grows as the task runs), `SAVING the recording ...` after Stop, and
  `Not recording` otherwise.
- The window never hides itself during a recording: the task runs on the participant screen, the app
  stays where it is.
- The large banner below it says what to do next, or what is wrong.

## What the warnings mean

| Message | What to do |
|---|---|
| The Physio Recorder window seems to have vanished | It does not close or hide itself during a recording. Look on the taskbar / the other screen (it may be behind the task). The state line says whether it is still recording. |
| `Internal error: see logs\app_<date>.log` (banner) | Something unexpected went wrong inside the app; the details are in that log. Recording usually goes on: check the state line, then tell the researcher and send the log. |
| `RECORDING ..., 0 RRST markers, last: none yet` for minutes | The task is not sending triggers: is it waiting at a prompt in MATLAB? Was it restarted after the recording started? Stop, and the new task run is picked up (recording restarts by itself). |
| `RRST: running, already recorded` (hub line) | This task run was already recorded, then stopped. Click Start recording to record it again, or restart the task (a new run starts recording by itself). |
| `RRST: no task markers recorded` (summary) | The task was not running while recording, or was still waiting at its start prompt. |
| `Could not connect to the hub 00:07:80:...` | Hub switched on? Charged? Within a few metres? OpenSignals closed? Is it **this PC's hub** (MAC on its label = the one shown)? If not, *Change*. Then *Check signals* again. |
| `No PLUX hub found` | The hub is not paired with this PC: switch it on, pair it in Windows Bluetooth settings (see *Devices on this PC*), then *Check signals* again. Or *Change* and type the MAC from its label. |
| `sensors have no ID chip, assuming ...` (hub line) | Normal for some sensors. Belt must be in port 1, ECG in port 2; a channel that stays `FLAT` is probably in the wrong port. |
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
- `rrst_figures.py <file> [--out <folder>]`: the RRST figures (task or bench), run by the app after Stop.
- Tests: `.venv\Scripts\python.exe -m pytest` (in `tests\`).
- `epoch.py <file> --stream Flow --events "interval\d_onset_" --pre 1 --post 3 --groups load null`:
  per-trial epochs + averages (RRST markers: see the rrst-toolkit README, "Event markers (LSL)").
- `segments.py <file> --start "level_(\w+)_ready" --end "level_{}_done"`: variable-length segments
  (wedge-level bench test).
- `plux_lsl.py --list` / `flow_lsl.py --list`: check that a device connects; prints the sensors detected.
- `plux_lsl.py --find`: the biosignalsplux hubs paired with this PC (their MACs); the app uses the same search.
  `plux_lsl.py --mac 00:07:80:xx:xx:xx` picks the hub (the command-line default is the original lab hub; the app
  always passes this PC's hub from `devices.json`).
- `devices.json` (per PC, not in git): `{"plux": true, "flow": true, "hub": "00:07:80:...", "ports": {"1": "RESP",
  "2": "ECG"}}`. `ports` is the sensor kind per hub port for sensors without an ID chip (missing = this standard
  wiring); the app passes it as `plux_lsl.py --port-kinds 1=RESP,2=ECG`. Chip sensors keep the chip's kind;
  with no sensor detected at all, the ports in the map are recorded. Assumed kinds are noted in the PLUX
  stream's metadata (`kinds_from_port_map`).
- Streams: `PLUX` (raw ADC counts, int32; column 0 `nSeq`), `Flow` (slm, degC; column 0 `nSeq`),
  `SessionMarkers` (`<task>_start` / `<task>_end` / `<task>_end_early`), task markers (e.g. `RRST`).
- Software: Python 3.13 venv in `.venv` (PLUX's API has no 3.11 build), LabRecorder 1.18 in `LabRecorder\`,
  PLUX API binaries in `vendor\plux\`.

## Setting up a new PC

1. Install Python 3.13 (64-bit) for the current user. It doesn't need to be on PATH.
2. From this folder: `py -3.13 -m venv .venv`, then `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
3. Download LabRecorder 1.18.0 for Windows (github.com/labstreaminglayer/App-LabRecorder, releases)
   and unzip it into `LabRecorder\`, so that `LabRecorder\LabRecorderCLI.exe` exists.
4. Pair this site's PLUX hub in Windows Bluetooth settings. Then check it: `.venv\Scripts\python.exe plux_lsl.py --find`
   shows its MAC, and `.venv\Scripts\python.exe plux_lsl.py --mac <MAC> --list` connects and prints the sensors.
5. Open the app and set **Devices** for this PC. Click **Check signals** once: it finds the hub and remembers it
   (**Hub:** line). If the sensors have no ID chip and the wiring is not belt 1 / ECG 2, set `"ports"` in `devices.json`.

Recordings in `data\` stay out of git, except the researchers' own pilot recordings listed in `.gitignore`.

## License

Apache 2.0; see [LICENSE](LICENSE). The PLUX API in `vendor/plux/` is distributed under its own Apache 2.0 licence.
