from rrst_markers import is_kids, kids_summary, parse_kids

ADULT = ["tutorial_start", "tutorial_end", "session_start",
         "trial_1_interval1_ready", "trial_1_interval1_onset_null", "trial_1_level_40",
         "trial_1_interval2_ready", "trial_1_interval2_onset_load", "trial_1_response",
         "trial_1_correct", "trial_1_confidence", "trial_1_iti", "session_end_complete"]


def slurps(*vols):
    """Markers of one trial's slurps: a volume, or "weak" / "overlong"."""
    out = []
    for k, v in enumerate(vols, 1):
        out.append(f"slurp_{k}_onset")
        out.append(f"slurp_{k}_{v}" if v in ("weak", "overlong") else f"slurp_{k}_end_{v:.2f}_100")
    return out


def trial(n, level, answer=None, correct=None, vols=(0.5, 0.5, 0.5)):
    out = [f"trial_{n}_level_{level}", f"trial_{n}_go", *slurps(*vols)]
    if answer:
        out += ["question", answer]
        if correct is not None:
            out.append(f"trial_{n}_{'correct' if correct else 'wrong'}")
    return out


# The current task: tutorial (free play, two demos), practice, main
KIDS = (["calibration_start", "calibration_end", "story", "tutorial_start", "freeplay_start",
         *slurps(0.4, "weak"), "freeplay_end",
         "demo_pinch_level_80", "trial_2_go", *slurps(0.3, 0.3, 0.3),
         "demo_sleep_level_0", "trial_3_go", *slurps(0.5, 0.5, 0.5), "tutorial_end",
         "practice_start",
         *trial(4, 80, "answer_yes", True), *trial(5, 0, "answer_no", True),
         "main_start",
         *trial(6, 60, "answer_no", False), *trial(7, 0, "answer_yes", False, vols=(0.5, "weak", 0.5, 0.4)),
         "trial_8_level_70", "trial_8_go", "slurp_1_onset", "bad_breath",
         "rest_start", "rest_end",
         *trial(9, 70, "answer_yes", True), "done"])


def test_recognises_the_kids_task():
    assert is_kids(KIDS)
    assert not is_kids(ADULT)
    assert not is_kids([])
    assert not is_kids(["RRST_ready"])
    assert is_kids(["calibration_start", "calibration_end", "story", "aborted"])   # aborted early
    # trial markers without interval markers (flow sensor off: no slurp markers)
    assert is_kids(["practice_start", "trial_1_level_0", "trial_1_go", "question", "answer_no"])
    assert not is_kids(ADULT[:6])                        # adult aborted after one interval


def test_parses_trials_slurps_and_phases():
    p = parse_kids(KIDS, times=range(len(KIDS)))
    by_n = {t["n"]: t for t in p["trials"] if t["n"] is not None}
    assert p["end"] == "done" and p["main_started"]
    free = [t for t in p["trials"] if t["phase"] == "freeplay"]
    assert len(free) == 1 and [s["status"] for s in free[0]["slurps"]] == ["counted", "weak"]
    assert by_n[2]["phase"] == "demo" and by_n[2]["demo"] == "pinch" and by_n[2]["level"] == 80
    assert by_n[3]["level"] == 0 and by_n[3]["go"] is not None
    assert by_n[4]["phase"] == "practice" and by_n[4]["answer"] == "yes" and by_n[4]["correct"] is True
    assert by_n[6]["phase"] == "main" and by_n[6]["correct"] is False
    s7 = by_n[7]["slurps"]
    assert [s["status"] for s in s7] == ["counted", "weak", "counted", "counted"]
    assert s7[3]["volume"] == 0.4 and s7[3]["peak"] == 100
    assert s7[0]["onset"] is not None and s7[0]["end"] is not None
    assert by_n[8]["answer"] == "bad" and by_n[8]["slurps"][0]["status"] is None
    assert by_n[9]["level"] == 70 and by_n[9]["correct"] is True
    assert free[0]["free"] and not any(t["free"] for t in p["trials"] if t["phase"] != "freeplay")


def test_summary_of_a_complete_session():
    lines = kids_summary(KIDS, session_end="rrst_end")
    text = " ".join(t for t, _ in lines)
    assert all(ok is not False for _, ok in lines)
    assert "RRST-Kids" in text and "finished" in text
    assert "practice 2 (2 correct, 0 wrong)" in text
    assert "main 4 (1 correct, 2 wrong)" in text            # 6, 7, 8 (bad breath), 9
    assert "slurps 25 (22 counted, 2 weak)" in text


def test_aborted_session_is_a_normal_outcome():
    values = ["calibration_start", "calibration_end", "story", "practice_start",
              *trial(1, 0), *trial(2, 80, "answer_no", False), *trial(3, 0, "answer_no", True),
              "trial_4_level_80", "trial_4_go", "slurp_1_onset", "aborted"]
    lines = kids_summary(values, session_end="rrst_end")
    assert all(ok is not False for _, ok in lines)
    assert "aborted by the experimenter after 2 trials" in lines[0][0]
    assert "practice only" in lines[0][0]


def test_recording_stopped_while_the_task_ran_is_not_a_failure():
    values = ["calibration_start", "calibration_end", "practice_start", *trial(1, 80, "answer_yes", True)]
    lines = kids_summary(values, session_end="rrst_end")
    assert lines[0][1] is None and "still running" in lines[0][0]
    lines = kids_summary(values, session_end=None)
    assert lines[0][1] is False


def test_missing_outcome_marker_is_flagged():
    values = ["practice_start", *trial(1, 80, "answer_yes"), "done"]   # no trial_1_correct
    lines = kids_summary(values, session_end="rrst_end")
    assert any(ok is False and "incomplete" in t for t, ok in lines)


def test_pilot_2026_10_30_practice_session():
    # sub-9999 run-2: an earlier version (no tutorial; free trials 1, 2, 9, 10 without a question),
    # two practice rounds, aborted during practice trial 13
    values = ["calibration_start", "calibration_end", "story", "practice_start",
              *trial(1, 0), *trial(2, 0), *trial(3, 80, "answer_no", False), *trial(4, 0, "answer_yes", False),
              *trial(5, 80, "answer_no", False), *trial(6, 0, "answer_no", True), *trial(7, 75, "answer_yes", True),
              *trial(8, 0, "answer_no", True), "practice_start", *trial(9, 0), *trial(10, 0),
              *trial(11, 80, "answer_yes", True), *trial(12, 0, "answer_no", True),
              "trial_13_level_80", "trial_13_go", *slurps(0.5, 0.5)[:4], "aborted"]
    assert is_kids(values)
    text = " ".join(t for t, _ in kids_summary(values, session_end="rrst_end"))
    assert "aborted by the experimenter after 8 trials (practice only, no main trials)" in text
    assert "practice 8 (5 correct, 3 wrong), main 0, 4 free trials" in text
    assert "slurps 38 (38 counted)" in text


def test_check_xdf_dispatches_kids_and_adult():
    from check_xdf import rrst_check
    kids = rrst_check(KIDS, "rrst_end")
    assert len(kids) == 2 and kids[0][0].startswith("RRST-Kids")
    adult = rrst_check(ADULT, "rrst_end")
    assert adult == [("RRST: 1 trials, 2 onsets, session_end_complete", True)]


def test_check_xdf_no_markers():
    from check_xdf import rrst_check
    [(text, ok)] = rrst_check([], "rrst_end")
    assert ok is False and "no task markers" in text
