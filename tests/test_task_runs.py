from task_runs import TaskRuns


def test_a_run_is_auto_started_once():
    runs = TaskRuns()
    assert runs.seen("A")
    assert runs.should_auto_start("checking", devices_ready=True)
    runs.mark_recorded("A")
    assert not runs.should_auto_start("checking", devices_ready=True)
    assert not runs.seen("A")  # same run again: no change


def test_only_while_checking_and_devices_ready():
    runs = TaskRuns()
    runs.seen("A")
    for state in ("idle", "connecting", "starting", "recording", "saving"):
        assert not runs.should_auto_start(state, devices_ready=True)
    assert not runs.should_auto_start("checking", devices_ready=False)


def test_nothing_online():
    runs = TaskRuns()
    assert not runs.seen(None)
    assert not runs.should_auto_start("checking", devices_ready=True)


def test_new_run_seen_while_saving_starts_once_back_to_checking():
    # 2026-10-30: Stop, then the task restarted while the app was still saving
    runs = TaskRuns()
    runs.seen("A")
    runs.mark_recorded("A")                  # recording of run A
    assert runs.seen("B")                    # task restarted while saving: new uid
    assert not runs.should_auto_start("saving", devices_ready=True)
    assert runs.is_new()
    assert runs.should_auto_start("checking", devices_ready=True)


def test_same_run_still_running_after_stop_is_not_restarted():
    runs = TaskRuns()
    runs.seen("A")
    runs.mark_recorded("A")
    runs.seen("A")                           # Stop pressed, task still running
    assert not runs.should_auto_start("checking", devices_ready=True)
    assert runs.current_recorded()


def test_run_gone_and_back_keeps_its_recorded_mark():
    runs = TaskRuns()
    runs.seen("A")
    runs.mark_recorded("A")
    assert runs.seen(None)                   # resolve missed it once
    assert runs.seen("A")
    assert not runs.should_auto_start("checking", devices_ready=True)


def test_mark_recorded_takes_every_stream_online_and_ignores_none():
    runs = TaskRuns()
    runs.mark_recorded(None, "A", "B")
    runs.seen("B")
    assert not runs.is_new()
    assert None not in runs.recorded
