import types

import physio_app
from physio_app import PRESETS, START_REST, START_TASK, App


def start_text(recording):
    return App.start_text(types.SimpleNamespace(preset=lambda: PRESETS.get(recording)))


def test_start_button_says_manual_for_tasks_only():
    assert start_text("HRD") == START_TASK == "2   Start manually (only if it did not start by itself)"
    assert start_text("RRST") == START_TASK
    assert start_text("Effort task (dynamometer)") == START_TASK
    assert start_text("Rest - baseline") == START_REST == "2   Start recording"
    assert start_text("pilotbreathing") == START_REST   # a typed label is a timed recording


def test_battery_unknown_is_red_and_says_unplug_the_charger():
    assert physio_app.battery_text("Connected\nBattery: 64%\n") == ("battery 64%", "black")
    assert physio_app.battery_text("Battery: 12%") == ("battery 12%", physio_app.BAD)
    assert physio_app.battery_text("Connected\n") == (
        "battery unknown: UNPLUG THE HUB FROM THE CHARGER before sensors touch the participant", physio_app.BAD)


def test_sticky_lines_stay_on_top_of_the_routine_banner():
    banner = types.SimpleNamespace(text=None, fg=None)
    fake = types.SimpleNamespace(banner=types.SimpleNamespace(config=lambda text, fg: banner.__dict__.update(
        text=text, fg=fg)), error_until=0, alert=None, banner_note=None)
    App.set_banner(fake, "All signals good - ready to record.", physio_app.OK)
    assert (banner.text, banner.fg) == ("All signals good - ready to record.", physio_app.OK)
    fake.banner_note = ("Recording stopped AUTOMATICALLY: HRD closed before it finished. Recording saved.",
                        physio_app.WARN)
    App.set_banner(fake, "All signals good - ready to record.", physio_app.OK)
    assert banner.text.splitlines() == [fake.banner_note[0], "All signals good - ready to record."]
    assert banner.fg == physio_app.WARN
    fake.alert = "HRD started - NOT RECORDING: fill in the form (...). Recording then starts by itself."
    App.set_banner(fake, "All signals good - ready to record.", physio_app.OK)
    assert banner.text.splitlines()[0] == fake.alert
    assert banner.fg == physio_app.BAD
