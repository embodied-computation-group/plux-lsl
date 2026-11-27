import pytest

from auto_stop import END_GRACE_S, GONE_S, AutoStop, is_end_marker


@pytest.mark.parametrize("label, prefix", [
    ("HRD_end", "HRD_end"),
    ("HRD_end_early", "HRD_end"),
    ("HRD_end_early participant pressed escape", "HRD_end"),
    ("session_end_complete", "session_end"),
    ("session_end_aborted", "session_end"),
    ("task_end", "task_end"),
    ("task_end_early", "task_end"),
    ("  task_end  ", "task_end"),
])
def test_end_markers(label, prefix):
    assert is_end_marker(label, prefix)


@pytest.mark.parametrize("label, prefix", [
    ("trial_end", "task_end"),
    ("tutorial_end", "task_end"),
    ("break_end", "task_end"),
    ("trial_12_response", "task_end"),
    ("trial_end", "session_end"),
    ("trial_end", "HRD_end"),
    ("block_1_end", "task_end"),
    ("HRD_start", "HRD_end"),
    ("reason HRD_end", "HRD_end"),   # only the first token counts
    ("", "HRD_end"),
    (None, "HRD_end"),
    ("task_end", None),              # no prefix (rest presets): never
    ("task_end", ""),
])
def test_not_end_markers(label, prefix):
    assert not is_end_marker(label, prefix)


def test_every_task_preset_has_an_end_prefix_and_rest_presets_none():
    import physio_app
    ends = {name: p.get("end") for name, p in physio_app.PRESETS.items()}
    assert ends == {"Rest - baseline": None, "Rest - peak": None, "RRST": "session_end",
                    "Effort task (dynamometer)": "task_end", "HRD": "HRD_end"}


def test_stops_after_the_grace_following_the_end_marker():
    stop = AutoStop()
    stop.resolved(True, 0)
    assert stop.reason(100) is None
    stop.ended("HRD_end", 100)
    assert stop.reason(100) is None
    assert stop.reason(100 + END_GRACE_S - 0.1) is None
    assert stop.reason(100 + END_GRACE_S) == "ended (HRD_end)"


def test_only_the_first_end_marker_counts():
    stop = AutoStop()
    stop.ended("HRD_end_early", 10)
    stop.ended("HRD_end", 14)
    assert stop.reason(10 + END_GRACE_S) == "ended (HRD_end_early)"


def test_end_marker_grace_is_kept_even_if_the_stream_goes():
    stop = AutoStop(gone_misses=1, gone_s=0)
    stop.resolved(True, 0)
    stop.ended("task_end", 1)
    stop.resolved(False, 2)          # task closed right after its end marker
    assert stop.reason(2) is None    # still within the grace
    assert stop.reason(1 + END_GRACE_S) == "ended (task_end)"


def resolves(stop, start, end, present, every=4):
    t = start
    while t <= end:
        stop.resolved(present, t)
        t += every
    return t - every


def test_stream_gone_for_long_enough_stops():
    stop = AutoStop()
    resolves(stop, 0, 20, True)
    last = resolves(stop, 24, 24 + GONE_S + 4, False)
    assert stop.reason(last) == "trigger stream gone"


def test_a_single_missed_resolve_never_stops():
    stop = AutoStop()
    stop.resolved(True, 0)
    stop.resolved(False, 4)
    assert stop.reason(4 + 1000) is None          # however long until the next resolve
    stop.resolved(True, 1004)
    stop.resolved(False, 1008)
    assert stop.reason(2000) is None


def test_two_misses_far_apart_are_not_enough():
    stop = AutoStop()
    stop.resolved(True, 0)
    stop.resolved(False, 4)
    stop.resolved(False, 4 + GONE_S + 10)
    assert stop.reason(100) is None
    stop.resolved(False, 4 + GONE_S + 14)         # third miss in a row
    assert stop.reason(100) == "trigger stream gone"


def test_misses_quickly_in_a_row_are_not_enough():
    stop = AutoStop()
    stop.resolved(True, 0)
    for t in (1, 2, 3, 4, 5):
        stop.resolved(False, t)
    assert stop.reason(5) is None                 # under GONE_S since the first miss


def test_stream_back_resets_the_count():
    stop = AutoStop()
    stop.resolved(True, 0)
    resolves(stop, 4, 12, False)
    stop.resolved(True, 16)
    last = resolves(stop, 20, 20 + GONE_S - 4, False)
    assert stop.reason(last) is None


def test_stream_never_seen_during_this_recording_never_stops():
    # e.g. Start recording clicked before the task was started ("record without triggers anyway")
    stop = AutoStop()
    last = resolves(stop, 0, 120, False)
    assert stop.reason(last) is None
