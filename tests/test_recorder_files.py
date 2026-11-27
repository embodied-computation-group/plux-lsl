import datetime
import os

from recorder import already_recorded_text, output_path, previous_recording


def touch(path, when):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    t = when.timestamp()
    os.utime(path, (t, t))
    return path


def test_no_previous_recording_and_no_folder_created(tmp_path):
    assert previous_recording(tmp_path, "01", "1", "hrd") is None
    assert not (tmp_path / "sub-01").exists()


def test_previous_recording_is_the_newest_of_the_plain_and_run_files(tmp_path):
    folder = tmp_path / "sub-01" / "ses-1"
    first = touch(folder / "sub-01_ses-1_task-hrd_physio.xdf", datetime.datetime(2026, 11, 27, 10, 0))
    assert previous_recording(tmp_path, "01", "1", "hrd") == first
    second = touch(folder / "sub-01_ses-1_task-hrd_run-2_physio.xdf", datetime.datetime(2026, 11, 27, 10, 30))
    assert previous_recording(tmp_path, "01", "1", "hrd") == second


def test_other_tasks_sessions_and_files_do_not_count(tmp_path):
    folder = tmp_path / "sub-01" / "ses-1"
    when = datetime.datetime(2026, 11, 27, 10, 0)
    touch(folder / "sub-01_ses-1_task-hrdx_physio.xdf", when)          # another task label
    touch(folder / "sub-01_ses-1_task-hrd_physio_figures" / "x.png", when)
    touch(folder / "sub-01_ses-1_task-hrd_physio_20261127-100000.labrecorder.log", when)
    touch(tmp_path / "sub-01" / "ses-2" / "sub-01_ses-2_task-hrd_physio.xdf", when)
    assert previous_recording(tmp_path, "01", "1", "hrd") is None


def test_repeat_is_detected_for_the_file_output_path_would_avoid(tmp_path):
    first = output_path(tmp_path, "01", "1", "rrst")
    first.write_bytes(b"")
    assert previous_recording(tmp_path, "01", "1", "rrst") == first
    assert output_path(tmp_path, "01", "1", "rrst").name == "sub-01_ses-1_task-rrst_run-2_physio.xdf"


def test_already_recorded_text(tmp_path):
    path = touch(tmp_path / "sub-01_ses-1_task-hrd_physio.xdf", datetime.datetime(2026, 11, 27, 9, 5))
    assert (already_recorded_text("01", "1", "hrd", path, today=datetime.date(2026, 11, 27))
            == "sub-01 ses-1 hrd was already recorded at 09:05 (sub-01_ses-1_task-hrd_physio.xdf)")
    assert (already_recorded_text("01", "1", "hrd", path, today=datetime.date(2026, 11, 28))
            == "sub-01 ses-1 hrd was already recorded at 2026-11-27 09:05 (sub-01_ses-1_task-hrd_physio.xdf)")
