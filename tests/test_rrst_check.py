ADULT = ["tutorial_start", "tutorial_end", "session_start",
         "trial_1_interval1_ready", "trial_1_interval1_onset_null", "trial_1_level_40",
         "trial_1_interval2_ready", "trial_1_interval2_onset_load", "trial_1_response",
         "trial_1_correct", "trial_1_confidence", "trial_1_iti", "session_end_complete"]


def test_check_xdf_adult():
    from check_xdf import rrst_check
    adult = rrst_check(ADULT, "rrst_end")
    assert adult == [("RRST: 1 trials, 2 onsets, session_end_complete", True)]


def test_check_xdf_no_markers():
    from check_xdf import rrst_check
    [(text, ok)] = rrst_check([], "rrst_end")
    assert ok is False and "no task markers" in text
