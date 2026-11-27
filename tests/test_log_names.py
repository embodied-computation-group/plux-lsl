import datetime
from pathlib import Path

from recorder import LOG_DIR, app_log_path, labrecorder_log_path, log_stamp

WHEN = datetime.datetime(2026, 11, 27, 9, 5, 7)


def test_stamp():
    assert log_stamp(WHEN) == "20261127-090507"


def test_app_log_is_one_file_per_start():
    assert app_log_path(WHEN) == LOG_DIR / "app_20261127-090507.log"
    assert app_log_path(WHEN, log_dir="x") == Path("x") / "app_20261127-090507.log"
    assert app_log_path(WHEN) != app_log_path(WHEN + datetime.timedelta(seconds=1))


def test_labrecorder_log_next_to_the_recording_with_a_timestamp():
    xdf = Path("data") / "sub-01" / "ses-1" / "sub-01_ses-1_task-hrd_run-2_physio.xdf"
    assert (labrecorder_log_path(xdf, WHEN)
            == xdf.parent / "sub-01_ses-1_task-hrd_run-2_physio_20261127-090507.labrecorder.log")


def test_physio_app_logs_to_a_timestamped_file():
    import physio_app
    assert physio_app.LOG_FILE.parent == LOG_DIR
    datetime.datetime.strptime(physio_app.LOG_FILE.name, "app_%Y%m%d-%H%M%S.log")
