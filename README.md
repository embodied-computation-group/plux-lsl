# Physio Recorder (PLUX + flow, LSL)

Records PLUX biosignals (ECG, respiration belt) and, for RRST, the airflow sensor, together
with task triggers, into one `.xdf` file per recording. No OpenSignals needed.

Data: `plux-lsl\data\sub-<ID>\ses-<N>\sub-<ID>_ses-<N>_task-<recording>_physio.xdf`
(a repeat never overwrites; it gets `_run-2`, `_run-3`, ...). Logs for troubleshooting: `plux-lsl\logs\`.

---

## Session Runner (the whole session, step by step)

A second, small window that walks you through the session in the protocol's order and opens each task
for you. It does **not** record anything and does not control the Physio Recorder: keep the Physio
Recorder open beside it (it starts and stops recording by itself, as in the checklists below). The
checklists below have the details for each task.

Open **Session Runner** (desktop shortcut, or `windows\session_runner.bat`).

**Screen 1: New session.**
1. **Protocol**: the list of tasks for today (*Full battery*, *Physio, HRD, RRST* or *RRST only*;
   usually already picked).
2. **RA initials** (2 to 4 letters), **Participant ID** and **Session number** (1, 2, ...; type `1`,
   not `01`). The participant ID is **exactly 4 digits** (`0001` to `9999`, e.g. `0007`, never `7`)
   whenever the protocol includes RRST: RRST names its files with 4 digits, so every task must get the
   same 4-digit ID. Otherwise letters and digits, no dashes or spaces.
3. Click **Next**, check the summary, click **Yes**. A red line under the fields says what to fix.

From now on the participant, session and your initials are shown at the top of every screen.

**Screen 2: Device check.** One light per device the protocol needs:
- **Physio Recorder is open**: open it from the desktop if red.
- **RRST device (USB)** and **Flow sensor SensorBridge (USB)**: plug in the USB cable if red.

Fix a red light, then **Check again**. **Continue** works only when all are green. If a device is
really not needed today, **Continue anyway** asks for a one-line reason, which goes into the session log.

**Screen 3, 4, ...: one screen per step** (e.g. *Step 2 of 4: HRD*).
1. Read the instructions. Do what the tick boxes say and tick each one; **Start** stays grey until all
   are ticked.
2. The yellow box says **what to type into the task when it asks** (and what to pick in the Physio
   Recorder). Type exactly that. RRST asks three questions in the MATLAB window (subject ID, session
   number, language `e`): type each value and press Enter.
3. Click **Start**. The task opens (for the resting recording nothing opens: you start it in the Physio
   Recorder). The Session Runner stays usable while the task runs.
   - *Could not start the task* (or a red line on the screen saying the launch target is not set up):
     open the task from its desktop shortcut, then click **Yes**; the log notes it was opened by hand.
     Tell the researcher.
4. When the task has finished and the Physio Recorder shows PASS, click **Done**: the next step opens.
   - Something went wrong: click **Problem...** and type one line. Then **Start again (repeat)** once it
     is fixed (it asks first, and is logged as a repeat), or **Next step** to go on.
   - Not doing this step today: **Skip step...** asks for a reason.
   - **Back** goes to the previous step (to repeat it, for example).
5. **All steps** (the list on the left) shows every step with its status: *not started*, *started*,
   *done*, *problem* or *skipped*; the step on screen is highlighted. Click any step to open it, in any
   order (for example when today's order differs from the protocol's). **Next step** and **Done** still
   go to the step below it in the list. Opening a step out of order is logged (`GOTO`).

**Last screen: Session finished.** A table of every step: done, skipped or problem, attempts, times
and notes, and the order the steps were started in. A step never opened shows *not started*. A red line
means some steps were not done: tell the researcher. **Close** when finished.

Closing the window early asks first; the log so far is kept, and tasks that are running keep running.
`DEMO MODE` at the top means nothing real is opened (every Start opens Notepad); never use it with a
participant.

The session log (plain text and JSON) is in `plux-lsl\logs\session_runner\`, one pair per session:
`session-runner_sub-<ID>_ses-<N>_<date>-<time>.txt` / `.json`. It lists every click with its time:
device check, Start, repeats, Done, problems, skips. If the top of the screen turns red with *SESSION
LOG NOT SAVED*, write things down on paper and tell the researcher.

### Session Runner: protocol files (researchers)

One file per protocol in `protocols\`, named by content: `full-battery.json` (rest, HRD, RRST, effort),
`physio-hrd-rrst.json` (rest, HRD, RRST), `rrst-only.json`. The step orders are provisional. Keys
starting with `_` are notes and are ignored; any other unknown key is an error (catches typos).

```json
{
  "name": "Physio, HRD, RRST",
  "order_status": "Step order PROVISIONAL - confirm with Micah",
  "log_dir": "logs/session_runner",
  "devices": ["physio_recorder", "rrst_teensy", "sensorbridge"],
  "participant_digits_only": true,
  "steps": [
    {
      "id": "hrd",
      "title": "HRD (heart rate discrimination)",
      "instructions": "In the Physio Recorder pick HRD, ... Then press Start here: HRD opens. ...",
      "prompts": ["Physio Recorder: HRD picked, every channel title green"],
      "launch": {"open": "HRD (Heart Rate Discrimination).lnk"},
      "type_into_task": [["HRD > Participant (letters and digits)", "{participant}"],
                         ["HRD > Session (letters and digits)", "{session}"]]
    }
  ]
}
```

- `devices`: any of `physio_recorder` (a window titled *Physio Recorder*), `rrst_teensy` (USB
  16C0:0483), `sensorbridge` (USB 0403:7168). Read-only checks.
- `launch`: `null` (nothing to open); `{"open": "<file name>"}`, a shortcut on the current user's
  Desktop (`%USERPROFILE%\Desktop\<file name>`), opened as a double-click would; `{"open": "<path>"}`
  with a folder (a `.lnk`, `.bat` or `.exe` anywhere); or `{"command": ["<program>", "arg", ...],
  "cwd": "<folder>"}`. `%VARS%` are expanded. The shipped protocols use the laptops' shortcuts
  `HRD (Heart Rate Discrimination).lnk`, `RRST (MATLAB).lnk` and `Effort task (dynamometer).lnk`. A
  missing target (or a value containing `FILL IN`) shows a red line on the step, and the RA opens the
  task by hand.
- `participant_digits_only`: `true` in every protocol with RRST; the start screen then accepts only a
  participant ID of exactly 4 digits (`0001` to `9999`). RRST reads the subject ID as a number and
  names its files `RRST_%04d`, so `1` and `0001` would not match the Physio Recorder's and HRD's files.
- `type_into_task`: `[what the task asks, value]` pairs; `{participant}`, `{session}`, `{initials}` are
  replaced by what the RA entered.
- `log_dir`: relative to the plux-lsl folder (default `logs/session_runner`); never inside a `data` folder.
- `.venv\Scripts\python.exe session_runner.py --check full-battery`: no window; lists each step's
  launch target as `ok` or `TODO` (placeholder or missing file) and ends with *Everything is set up.*
  when all are found. Run it on every laptop, signed in as the account the RA uses.
- `windows\session_runner.bat full-battery` opens with that protocol picked; with no argument the RA
  picks it from the list. `windows\session_runner.bat --demo` opens the demo (every Start opens
  Notepad, log names end in `_demo`).

---

## RA checklist: resting recording (15 min, baseline or peak)

1. **Hub off the charger, then on.** Unplug the charger first: never put sensors on anyone, or record,
   while the hub is on the charger. Press the button on the biosignalsplux hub; its light blinks.
   Battery shows in the app (a red `battery unknown: UNPLUG THE HUB FROM THE CHARGER ...` usually means
   it is still on the charger).
2. **Sensors on the participant.** Respiration belt around the lower chest, snug but comfortable.
   ECG electrodes on clean, dry skin, leads clipped on (see *ECG electrode placement* below):
   **red under the right collarbone, black on the lower left ribs, white on a bony spot** (left
   collarbone or hip bone). Cables into the hub: **belt in port 1, ECG in port 2**.
3. Open **Physio Recorder** (desktop shortcut).
4. Fill in **Participant** and **Session**, and pick **Rest - baseline** or **Rest - peak**
   (15 min is filled in).
5. Click **1 Check signals**. Wait about 15 s.
   - Every channel title should turn **green** (e.g. `ECG2 HR 68 bpm`, `RESP1 14 breaths/min`).
   - Amber or red means fix it first. See *What the warnings mean* below.
6. Click **2 Start recording**. The screen turns grey with instructions for the participant.
   - If this participant, session and recording were already recorded, the app first asks
     *... was already recorded at HH:MM (file). Record again as a new file?* **No** unless you mean to
     repeat it (a repeat is saved as a new `_run-2` file, never over the first).
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
   - If it doesn't start (red banner, e.g. flow sensor not connected, Participant not filled in): fix it,
     and recording starts by itself. Or click **2 Start manually** to record without the flow sensor.
6. **Recording stops by itself** a few seconds after RRST finishes (*Recording stopped AUTOMATICALLY:
   RRST ended (session_end_...)*). Only if it doesn't stop: click **Stop recording**, then confirm.
7. Check the **Last recording** panel: PASS / CHECK. `RRST triggers: N (k onsets)`: each trial sends
   about 9 triggers, 2 of them onsets (one per breath). The pilot's 100 trials give about 900 triggers
   and 200 onsets, plus a few more for repeated trials. A shorter or aborted run gives fewer.

## RA checklist: effort task (dynamometer)

1. **Hub on.** Press the button on the biosignalsplux hub; its light blinks.
2. Sensors on the participant (belt, ECG) as above: belt in port 1, ECG in port 2. No flow sensor.
3. Open **Physio Recorder**, fill in Participant/Session, pick **Effort task (dynamometer)**.
4. Click **1 Check signals**. Wait about 15 s: every channel title should turn **green**.
5. **Start the effort task** (PsychoPy). **Recording starts by itself** as soon as the task is running
   (the app beeps and says *Recording started AUTOMATICALLY*); the task waits until the recorder receives
   its triggers. Run the task as normal. The state line counts the `EffortReward markers` as they come in.
   - If it doesn't start (red banner): fix what it says, and recording starts by itself. Or click
     **2 Start manually**.
6. **Recording stops by itself** a few seconds after the task finishes (*Recording stopped AUTOMATICALLY:
   EffortReward ended (task_end)*). Only if it doesn't stop: click **Stop recording**, then confirm.
7. Check the **Last recording** panel: PASS / CHECK. `EffortReward triggers: N` should be well above 0;
   `0`, or no `EffortReward` line at all, means the task's triggers were not recorded: note it and tell
   the researcher.

## RA checklist: HRD (heart rate discrimination)

The ECG and breathing belt are recorded during HRD whichever heart signal HRD uses (PLUX ECG or the
Nonin backup), so the PLUX hub and sensors go on in both cases.

1. **Hub off the charger, then on.** Sensors on the participant as above (belt in port 1, ECG in port 2).
   With the Nonin backup: Nonin plugged in, finger in, as well.
2. Open **Physio Recorder**, fill in Participant/Session, pick **HRD**. Click **1 Check signals**; wait
   until every channel title is **green**.
3. Open **HRD** (desktop shortcut), fill in its dialog. **Recording starts by itself** when HRD starts
   (the app beeps and says *Recording started AUTOMATICALLY*). Run the task as normal.
4. **Recording stops by itself** a few seconds after HRD finishes (*Recording stopped AUTOMATICALLY:
   HRD ended (HRD_end)*). Only if it doesn't stop: click **Stop recording**, then confirm.
5. Check the **Last recording** panel: PASS / CHECK, and `HRD triggers: N` above 0.

Needs an HRD launcher that sends the `HRD` trigger stream; with an older launcher the recording does not
start or stop by itself (click **2 Start manually** after HRD has started, **Stop recording** when it ends).

**All three tasks:** the recording also stops by itself if the task crashes or is closed (its trigger
stream gone for about 15 s): *Recording stopped AUTOMATICALLY: ... closed before it finished. Recording
saved.* Note it and tell the researcher. If you click **2 Start manually** for a task that was already
recorded for this participant and session, the app asks first (answer **No** unless you mean to repeat
it). A repeat that starts by itself is recorded without asking. Either way a repeat is a new file, and
the banner names both files: *REPEAT: ... was already recorded at 10:12 (earlier file). This recording
is a new file: ..._run-2_physio.xdf*.

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

**ECG electrode placement** (lab ECG sensor, checked 2026-11-27 on a test recording: R and T waves
upright, HR 84):

| Lead | Where |
|---|---|
| **Red** | just under the **right** collarbone |
| **Black** | **left** side, on the lower ribs |
| **White** (reference) | a bony spot away from the heart: left collarbone or hip bone |

In *Check signals*, the ECG trace must show tall, sharp spikes pointing **up**, each followed by a
smaller rounded bump (also up). Spikes pointing **down** mean red and black are swapped: swap them
and *Check signals* again. With this placement the R peaks are large (in the test, close to the top
of the hub's range); if the ECG title says `CLIPPING`, move red and black a little closer together.

**Sensor cables:** belt in **port 1**, ECG in **port 2** of the hub (the lab's standard wiring). Sensors
with an ID chip are recognised on any port. Sensors **without** an ID chip are not: the app then assumes
the standard wiring, and the hub line says *sensors have no ID chip, assuming breathing belt (RESP) on
port 1, ECG on port 2*. If a channel then says `FLAT`, check the cable is in the right port. A site that
wires differently: the researcher sets `"ports"` in `devices.json`, e.g. `"ports": {"1": "RESP", "2": "ECG"}`.

## What the window shows

- The line under the hub status says what is happening, at a glance:
  `RECORDING sub-01 ses-1 since 10:39 (4:12), 57 RRST markers, last: trial_5_go` while a task is recorded
  (the time ticks; the marker count grows as the task runs), `SAVING the recording ...` after it stops,
  and `Not recording` otherwise.
- The window never hides itself during a recording: the task runs on the participant screen, the app
  stays where it is.
- The large banner below it says what to do next, or what is wrong. Important lines (a task that could
  not start recording, *Recording stopped AUTOMATICALLY*, *REPEAT*) stay at the top of the banner until
  they no longer apply, the next recording starts, or you change Participant, Session or Recording.
- Participant, Session and Recording cannot be changed while a recording starts, runs or is saved.

## What the warnings mean

| Message | What to do |
|---|---|
| The Physio Recorder window seems to have vanished | It does not close or hide itself during a recording. Look on the taskbar / the other screen (it may be behind the task). The state line says whether it is still recording. |
| `Internal error: see logs\app_<date>-<time>.log` (banner) | Something unexpected went wrong inside the app; the details are in that log. Recording usually goes on: check the state line, then tell the researcher and send the log. |
| `RECORDING ..., 0 RRST markers, last: none yet` for minutes | The task is not sending triggers: is it waiting at a prompt in MATLAB? Was it restarted after the recording started? Then the recording stops by itself about 20 s later and the new task run starts recording by itself; if not, Stop, and the new run is picked up. |
| `Recording stopped AUTOMATICALLY: RRST ended (session_end_...)` (or `HRD ended`, `EffortReward ended`) | Normal: the task sent its end marker; the recording went on 5 s more, then stopped. Check the summary. |
| `Recording stopped AUTOMATICALLY: ... closed before it finished. Recording saved.` | The task closed or crashed without its end marker. The data up to then is saved. Note it and tell the researcher. A restarted task starts a new recording by itself. |
| `... was already recorded at HH:MM (...). Record again as a new file?` | This participant, session and recording already has a file. **No** if you clicked Start by mistake; **Yes** to repeat it (saved as `_run-2`, `_run-3`, ...). |
| `REPEAT: ... was already recorded at ... This recording is a new file: ...` (banner) | This recording repeats an earlier one (confirmed by you, or the task was started again and recorded by itself). Both files are kept; note which is which in the session log. |
| `<task> started - NOT RECORDING: fill in the form (...)` (red, top of the banner) | The task is running but the form is incomplete. Fill in Participant/Session: recording starts by itself a few seconds after you stop typing. |
| `RRST: running, already recorded` (hub line) | This task run was already recorded, then stopped. Click Start manually to record it again, or restart the task (a new run starts recording by itself). |
| `RRST: no task markers recorded` (summary) | The task was not running while recording, or was still waiting at its start prompt. |
| `EffortReward triggers: 0` (summary) | The effort task was not running while recording, or was still at its first screen. |
| `battery unknown: UNPLUG THE HUB FROM THE CHARGER ...` (hub line, red) | The hub is probably on the charger. Unplug it before sensors touch the participant; *Disconnect* and *Check signals* again, and the battery then shows. |
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
| `RRST is waiting for the recorder, but the flow sensor is not connected` | *Retry flow sensor* (USB cable, SEK-ControlCenter closed); recording then starts by itself. Or click Start manually to record without flow. |
| `Dropped samples` > 0 | Bluetooth dropouts: keep the hub close to the laptop and charged. |

## For researchers

- `record.py`: command-line recorder (`--sub 01 --ses 1 --minutes 5`, or `--task rrst` to record until Ctrl+C).
- `check_xdf.py <file>`: the PASS/CHECK summary for any recording.
- Logs are never overwritten: the app writes `logs\app_YYYYMMDD-HHMMSS.log` per start (the hub and flow
  streamers their own `logs\hub_...` / `logs\flow_...`), and LabRecorder writes
  `<recording>_physio_YYYYMMDD-HHMMSS.labrecorder.log` next to each recording.
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
  `SessionMarkers` (`<task>_start` / `<task>_end` / `<task>_end_early`), task markers (e.g. `RRST`, `EffortReward`).
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
