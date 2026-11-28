import datetime
import json
import types

import pytest

import session_runner as sr
from session_runner import ProtocolError

STEP = {"id": "hrd", "title": "HRD", "instructions": "Do it.", "prompts": ["Electrodes on"],
        "launch": {"open": "C:/nowhere/HRD.lnk"}, "type_into_task": [["Participant", "{participant}"]]}


def protocol_raw(**changes):
    raw = {"name": "Test protocol", "order_status": "PROVISIONAL", "devices": ["physio_recorder"],
           "steps": [dict(STEP), dict(STEP, id="rrst", title="RRST", launch=None)]}
    raw.update(changes)
    return raw


def write(folder, name, data):
    path = folder / f"{name}.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def problems_of(raw, root):
    with pytest.raises(ProtocolError) as e:
        sr.validate_protocol(raw, root)
    return str(e.value)


# ---- protocol files

SHORTCUTS = {"hrd": "HRD (Heart Rate Discrimination).lnk", "rrst": "RRST (MATLAB).lnk",
             "effort": "Effort task (dynamometer).lnk"}
ORDERS = {"full-battery": ["rest", "hrd", "rrst", "effort"], "physio-hrd-rrst": ["rest", "hrd", "rrst"],
          "rrst-only": ["rrst"]}


def test_shipped_protocols_load_and_are_marked_provisional():
    assert set(ORDERS) <= set(sr.protocol_names())
    for name, order in ORDERS.items():
        p = sr.load_protocol(name)
        assert [s["id"] for s in p["steps"]] == order
        assert "PROVISIONAL" in p["order_status"]
        assert p["log_dir"] == (sr.ROOT / "logs" / "session_runner").resolve()
        assert p["participant_digits_only"] is True   # every shipped protocol contains RRST
        for step in p["steps"]:   # nothing to open, or a shortcut file name on the Desktop
            assert step["launch"] is None if step["id"] == "rest" else step["launch"] == {"open": SHORTCUTS[step["id"]]}


def test_shipped_protocols_name_no_site():
    for path in sr.PROTOCOL_DIR.glob("*.json"):
        text = path.read_text(encoding="utf-8").lower()
        for site in ("maastricht", "zurich", "boris", "aarhus", "site"):
            assert site not in text, (path.name, site)


def test_rrst_box_shows_subject_id_session_and_language():
    rrst = sr.load_protocol("rrst-only")["steps"][0]
    typed = dict(rrst["type_into_task"])
    assert (typed["RRST > subject ID"], typed["RRST > Session number"], typed["RRST > Language"]) == (
        "{participant}", "{session}", "e")


def test_valid_protocol_with_defaults(tmp_path):
    p = sr.validate_protocol(protocol_raw(), tmp_path)
    assert p["name"] == "Test protocol" and p["participant_digits_only"] is False
    assert p["log_dir"] == (tmp_path / "logs" / "session_runner").resolve()
    assert [s["id"] for s in p["steps"]] == ["hrd", "rrst"]
    assert p["steps"][1]["launch"] is None


def test_notes_starting_with_underscore_are_ignored(tmp_path):
    raw = protocol_raw(_note="hello")
    raw["steps"][0]["_todo"] = "fill in the shortcut"
    assert sr.validate_protocol(raw, tmp_path)["steps"][0]["title"] == "HRD"


def test_every_problem_is_listed(tmp_path):
    raw = protocol_raw(name="", devices=["toaster"], colour="red", participant_digits_only="yes")
    raw["steps"] = [dict(STEP, id="Bad ID", title="", promts=["typo"]), dict(STEP, launch={"open": "a", "command": ["b"]}),
                    dict(STEP, type_into_task=[["Participant", "{participnat}"]])]
    del raw["steps"][2]["launch"]
    text = problems_of(raw, tmp_path)
    for expected in ("unknown setting 'colour'", '"name" missing', '"participant_digits_only" must be true or false', '"devices" must be', '"id" must be',
                     '"title" missing', "unknown setting 'promts'", 'exactly one of "open" or "command"',
                     "id used twice", '"launch" missing', "unknown {participnat}"):
        assert expected in text, expected


def test_no_steps_and_not_an_object(tmp_path):
    assert '"steps" missing or empty' in problems_of(protocol_raw(steps=[]), tmp_path)
    assert "one JSON object" in problems_of([1, 2], tmp_path)


def test_command_launch_must_be_a_list(tmp_path):
    raw = protocol_raw()
    raw["steps"][0]["launch"] = {"command": "notepad.exe"}
    assert '"command" must be a list' in problems_of(raw, tmp_path)
    raw["steps"][0]["launch"] = {"command": ["notepad.exe"], "cwd": "C:/"}
    assert sr.validate_protocol(raw, tmp_path)["steps"][0]["launch"]["cwd"] == "C:/"


def test_log_dir_never_inside_a_data_folder(tmp_path):
    for bad in ("data", "data/logs", str(tmp_path / "Data" / "x")):
        assert "inside a data folder" in problems_of(protocol_raw(log_dir=bad), tmp_path)
    assert sr.validate_protocol(protocol_raw(log_dir="reports/runner"), tmp_path)["log_dir"] == \
        (tmp_path / "reports" / "runner").resolve()


def test_missing_broken_and_named_files(tmp_path):
    write(tmp_path, "proto_a", protocol_raw())
    with pytest.raises(ProtocolError) as e:
        sr.load_protocol("nosuch", folder=tmp_path, root=tmp_path)
    assert str(e.value).startswith("Protocol file not found") and str(e.value).endswith(": proto_a")
    write(tmp_path, "broken", '{"name": "X",\n "steps": [}')
    with pytest.raises(ProtocolError, match=r"not valid JSON \(line 2"):
        sr.load_protocol("broken", folder=tmp_path, root=tmp_path)
    path = write(tmp_path, "good", protocol_raw())
    assert sr.load_protocol("good", folder=tmp_path, root=tmp_path)["file"] == str(path)
    assert sr.load_protocol(str(path), folder=tmp_path / "elsewhere", root=tmp_path)["name"] == "Test protocol"


def test_demo_replaces_every_launch_with_notepad(tmp_path):
    write(tmp_path, "good", protocol_raw())
    p = sr.load_protocol("good", folder=tmp_path, root=tmp_path, demo=True)
    assert p["steps"][0]["launch"] == {"command": ["notepad.exe"]}
    assert p["steps"][1]["launch"] is None   # a step with nothing to open stays that way


# ---- start screen input

def test_valid_start_input_is_cleaned():
    assert sr.validate_start(" jd ", " 0123 ", " 2 ") == ({"initials": "JD", "participant": "0123", "session": "2"}, [])
    assert sr.validate_start("abcd", "pilotA", "12")[1] == []


@pytest.mark.parametrize("initials,participant,session,field", [
    ("J", "0123", "1", "RA initials"), ("J.D", "0123", "1", "RA initials"), ("", "0123", "1", "RA initials"),
    ("JD", "sub-01", "1", "Participant ID"), ("JD", "01 23", "1", "Participant ID"), ("JD", "", "1", "Participant ID"),
    ("JD", "a_b", "1", "Participant ID"), ("JD", "x" * 21, "1", "Participant ID"),
    ("JD", "0123", "01", "Session"), ("JD", "0123", "0", "Session"), ("JD", "0123", "one", "Session"),
    ("JD", "0123", "", "Session"), ("JD", "0123", "100", "Session"),
])
def test_bad_start_input_names_the_field(initials, participant, session, field):
    _, problems = sr.validate_start(initials, participant, session)
    assert len(problems) == 1 and problems[0].startswith(field)


def test_four_digit_participant_for_rrst_protocols():
    for good in ("0001", "0007", "0123", "9999"):
        assert sr.validate_start("JD", good, "1", digits_only=True)[1] == []
    for bad in ("pilotA", "A001", "00 1", "", "1", "7", "007", "00007", "12345", "0000"):
        _, problems = sr.validate_start("JD", bad, "1", digits_only=True)
        assert problems == ["Participant: exactly 4 digits, e.g. 0007"], bad
    assert sr.validate_start("JD", "pilotA", "1", digits_only=False)[1] == []
    assert sr.validate_start("JD", "7", "1", digits_only=False)[1] == []   # no RRST: any letters and digits


def test_fill_only_known_tokens():
    who = {"initials": "JD", "participant": "0123", "session": "2"}
    assert sr.fill("{participant} / {session} / {initials} / {other}", who) == "0123 / 2 / JD / {other}"


# ---- launch targets

def test_launch_problems(tmp_path):
    assert sr.launch_problem(None) is None
    assert "not been set up" in sr.launch_problem({"open": "FILL IN: HRD shortcut"})
    assert "Cannot find" in sr.launch_problem({"open": str(tmp_path / "HRD.lnk")})
    (tmp_path / "HRD.lnk").write_text("x")
    assert sr.launch_problem({"open": str(tmp_path / "HRD.lnk")}) is None
    assert "Cannot find the program" in sr.launch_problem({"command": [str(tmp_path / "no.exe")]})
    assert "Cannot find the program" in sr.launch_problem({"command": ["surely-not-a-program-xyz"]})
    assert "Cannot find the folder" in sr.launch_problem({"command": [str(tmp_path / "HRD.lnk")],
                                                          "cwd": str(tmp_path / "nofolder")})


def test_bare_shortcut_name_is_looked_up_on_the_users_desktop(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    name = "RRST (MATLAB).lnk"
    assert sr.resolve_open(name) == str(tmp_path / "Desktop" / name)
    assert "Cannot find " + str(tmp_path / "Desktop" / name) in sr.launch_problem({"open": name})
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "Desktop" / name).write_text("x")
    assert sr.launch_problem({"open": name}) is None
    other = tmp_path / "elsewhere" / "task.bat"   # a path with a folder is used as it is
    assert sr.resolve_open(str(other)) == str(other)
    monkeypatch.setenv("TASKS", str(tmp_path / "elsewhere"))
    assert sr.resolve_open("%TASKS%\\task.bat") == str(tmp_path / "elsewhere") + "\\task.bat"


def test_start_launch_refuses_a_missing_target(tmp_path):
    with pytest.raises(OSError, match="Cannot find"):
        sr.start_launch({"open": str(tmp_path / "missing.lnk")})


# ---- device check

def test_device_check_with_injected_windows_and_ports():
    names = ["physio_recorder", "rrst_teensy", "sensorbridge"]
    r = sr.check_devices(names, titles=["Physio Recorder", "Notepad"], ports=[(0x16C0, 0x0483), (None, None)])
    assert r["physio_recorder"][0] and r["rrst_teensy"][0]
    assert not r["sensorbridge"][0] and "0403:7168" in r["sensorbridge"][1]
    r = sr.check_devices(names, titles=["Session Runner", "Physio Recorder - old"], ports=[])
    assert not any(ok for ok, _ in r.values())


def test_device_check_failure_is_red_not_a_crash(monkeypatch):
    monkeypatch.setattr(sr, "usb_ports", lambda: (_ for _ in ()).throw(RuntimeError("no serial")))
    r = sr.check_devices(["rrst_teensy"], titles=[])
    assert r["rrst_teensy"] == (False, "could not check (no serial). Plug the RRST device's USB cable into this laptop.")


# ---- session state and log

class Clock:
    def __init__(self):
        self.t = datetime.datetime(2026, 11, 30, 10, 0, 0)

    def __call__(self):
        self.t += datetime.timedelta(minutes=1)
        return self.t


def session(tmp_path, demo=False):
    p = sr.validate_protocol(protocol_raw(log_dir=str(tmp_path / "logs")), tmp_path)
    p["file"] = "test.json"
    return sr.Session(p, {"initials": "JD", "participant": "0123", "session": "2"}, demo=demo, now=Clock())


def test_log_files_are_named_per_session_and_never_reused(tmp_path):
    s = session(tmp_path)
    assert s.txt_path.name == "session-runner_sub-0123_ses-2_20261130-100100.txt"
    assert s.json_path.with_suffix(".txt") == s.txt_path
    assert s.txt_path.exists() and s.json_path.exists()
    txt, js = sr.log_paths(tmp_path / "logs", "0123", "2", datetime.datetime(2026, 11, 30, 10, 1, 0))
    assert txt.name == "session-runner_sub-0123_ses-2_20261130-100100-2.txt" and js.suffix == ".json"
    assert session(tmp_path, demo=True).txt_path.name.endswith("_demo.txt")


def test_whole_session_is_logged(tmp_path):
    s = session(tmp_path)
    s.record_devices({"physio_recorder": (False, "no window")}, skip_reason="recorder on the other laptop")
    assert s.start("hrd") == 1
    s.problem("hrd", "  HRD froze \n at trial 3 ")
    assert s.start("hrd") == 2     # a repeat
    s.done("hrd")
    s.skip("rrst", "device broken")
    s.finish("completed: all steps shown")

    data = json.loads(s.json_path.read_text(encoding="utf-8"))
    assert (data["participant"], data["session"], data["initials"], data["protocol"]) == ("0123", "2", "JD", "Test protocol")
    assert data["protocol_file"] == "test.json" and "site" not in data
    assert data["devices"]["all_ok"] is False and data["devices"]["skip_reason"] == "recorder on the other laptop"
    hrd, rrst = data["steps"]
    assert hrd["status"] == "done" and [a["outcome"] for a in hrd["attempts"]] == ["problem", "done"]
    assert hrd["notes"] == ["HRD froze at trial 3"]
    assert rrst["status"] == "skipped" and rrst["notes"] == ["skipped: device broken"]
    assert data["end_reason"] == "completed: all steps shown" and data["ended"]

    text = s.txt_path.read_text(encoding="utf-8")
    kinds = [line.split()[2] for line in text.splitlines()]
    assert kinds == ["SESSION", "DEVICES", "START", "PROBLEM", "REPEAT", "DONE", "SKIP", "END"]
    assert "CONTINUED WITHOUT ALL GREEN: recorder on the other laptop" in text
    assert "[hrd] attempt 2 (repeat)" in text
    assert not list(tmp_path.glob("logs/*.tmp"))

    assert s.summary() == [("HRD", "done", 2, "10:04", "10:10", "HRD froze at trial 3"),
                           ("RRST", "skipped", 0, "", "", "skipped: device broken")]


def test_failed_launch_then_opened_by_hand_is_logged(tmp_path):
    s = session(tmp_path)
    s.launch_failed("hrd", "Cannot find C:/nowhere/HRD.lnk")
    s.start("hrd", by_hand=True)
    text = s.txt_path.read_text(encoding="utf-8")
    assert "NOLAUNCH [hrd] Cannot find C:/nowhere/HRD.lnk" in text
    assert "START    [hrd] attempt 1 (task opened by hand)" in text
    assert s.step("hrd")["attempts"][0]["opened_by_hand"] is True


def test_problem_and_skip_need_a_note(tmp_path):
    s = session(tmp_path)
    with pytest.raises(ValueError):
        s.problem("hrd", "   ")
    with pytest.raises(ValueError):
        s.skip("hrd", "")


def test_finish_once_and_reopen(tmp_path):
    s = session(tmp_path)
    s.finish("completed")
    first = s.ended
    s.finish("closed by the RA")
    assert (s.ended, s.end_reason) == (first, "completed")
    s.reopen()
    assert s.ended is None
    s.finish("closed by the RA before the end (at step hrd)")
    assert s.end_reason.startswith("closed by the RA")


def test_unwritable_log_is_reported_not_raised(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    p = sr.validate_protocol(protocol_raw(log_dir=str(blocker / "logs")), tmp_path)
    s = sr.Session(p, {"initials": "JD", "participant": "0123", "session": "2"}, now=Clock())
    assert s.log_error.startswith("SESSION LOG NOT SAVED")
    s.start("hrd")   # the session goes on
    assert s.step("hrd")["status"] == "started"


def four_step_session(tmp_path):
    steps = [dict(STEP, id=sid, title=sid.upper(), launch=None) for sid in ("rest", "hrd", "rrst", "effort")]
    p = sr.validate_protocol(protocol_raw(log_dir=str(tmp_path / "logs"), steps=steps), tmp_path)
    p["file"] = "test.json"
    return sr.Session(p, {"initials": "JD", "participant": "0123", "session": "2"}, now=Clock())


def test_goto_is_logged_only_when_out_of_order(tmp_path):
    s = four_step_session(tmp_path)
    assert s.goto("rest", "rrst") is True           # forward past a step
    assert s.goto("rrst", "hrd") is False           # the previous step: what Back does
    assert s.goto("hrd", "rrst") is True            # the next step, but hrd is not finished (Next is grey)
    s.start("rrst")
    s.done("rrst")
    assert s.goto("rrst", "effort") is False        # the next step after Done: what Next step does
    assert s.goto("effort", "effort") is False      # the step already shown
    assert s.goto("effort", "rest") is True         # back to the start
    gotos = [e for e in s.events if e["event"] == "GOTO"]
    assert [(e["step"], e["text"]) for e in gotos] == [
        ("rrst", "opened out of order: from step 1 (rest, not started) to step 3 (rrst)"),
        ("rrst", "opened out of order: from step 2 (hrd, not started) to step 3 (rrst)"),
        ("rest", "opened out of order: from step 4 (effort, not started) to step 1 (rest)")]
    assert "GOTO     [rrst] opened out of order: from step 1 (rest" in s.txt_path.read_text(encoding="utf-8")


def test_summary_after_steps_in_any_order(tmp_path):
    s = four_step_session(tmp_path)
    s.goto("rest", "rrst")
    s.start("rrst")
    s.done("rrst")
    s.goto("rrst", "hrd")
    s.start("hrd")
    s.goto("hrd", "effort")   # hrd left running, effort skipped, rest never opened
    s.skip("effort", "no dynamometer")
    assert s.run_order() == ["rrst", "hrd"]
    assert s.open_steps() == [("rest", "not started"), ("hrd", "started")]
    assert s.completion() == "end screen with steps not finished: rest (not started), hrd (started)"
    s.finish(s.completion())
    rows = s.summary()
    assert [r[:3] for r in rows] == [("REST", "not started", 0), ("HRD", "started", 1), ("RRST", "done", 1),
                                    ("EFFORT", "skipped", 0)]   # protocol order, real statuses
    assert rows[0][3:5] == ("", "") and rows[1][4] == ""         # never visited / never ended: no times
    assert rows[2][3] < rows[1][3]                                 # rrst (step 3) ran before hrd (step 2)
    data = json.loads(s.json_path.read_text(encoding="utf-8"))
    assert data["run_order"] == ["rrst", "hrd"]
    assert data["end_reason"].startswith("end screen with steps not finished: rest")


def test_completion_when_every_step_is_finished(tmp_path):
    s = four_step_session(tmp_path)
    for sid in ("effort", "rest", "rrst"):
        s.start(sid)
        s.done(sid)
    s.skip("hrd", "participant tired")
    assert s.open_steps() == [] and s.completion() == "completed: all steps shown"
    assert s.run_order() == ["effort", "rest", "rrst"]


def test_error_file_goes_to_the_log_folder(tmp_path):
    where = sr.write_error("Traceback ...", tmp_path / "logs")
    assert where == str(tmp_path / "logs" / "session-runner_errors.txt")
    assert "Traceback ..." in (tmp_path / "logs" / "session-runner_errors.txt").read_text(encoding="utf-8")


def test_check_command_without_and_with_the_shortcuts(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert sr.main(["--check", "full-battery"]) == 1
    out = capsys.readouterr().out
    assert "TODO  hrd" in out and "TODO  rrst" in out and "TODO  effort" in out and "ok    rest" in out
    (tmp_path / "Desktop").mkdir()
    for name in SHORTCUTS.values():
        (tmp_path / "Desktop" / name).write_text("x")
    for protocol in ORDERS:
        assert sr.main(["--check", protocol]) == 0
        assert capsys.readouterr().out.rstrip().endswith("Everything is set up.")
    assert sr.main(["--check", "nosuch"]) == 2
