from check_xdf import marker_lines
from test_rrst_check import ADULT


def test_effort_task_gets_a_generic_trigger_count():
    values = ["block_1_start", "trial_1_offer", "trial_1_squeeze", "trial_1_reward", "block_1_end"]
    assert marker_lines("EffortReward", values) == [("EffortReward triggers: 5", True)]


def test_no_triggers_is_flagged():
    assert marker_lines("EffortReward", []) == [("EffortReward triggers: 0", False)]


def test_rrst_keeps_its_own_check():
    lines = marker_lines("RRST", ADULT)
    assert lines[0] == ("RRST triggers: 13 (2 onsets)", True)
    assert lines[1:] == [("RRST: 1 trials, 2 onsets, session_end_complete", True)]


def test_effort_preset_is_a_task_with_its_marker_stream_and_no_flow():
    import physio_app
    preset = physio_app.PRESETS["Effort task (dynamometer)"]
    assert preset == {"task": "effortreward", "minutes": None, "markers": "EffortReward", "flow": False,
                      "end": "task_end"}
    assert physio_app.label_to_task("Effort task (dynamometer)") == "effortreward"
    assert "effortreward" not in physio_app.FIGURES
