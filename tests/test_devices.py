import json

from devices import (auto_hub, candidate_hubs, find_hubs, load_devices, normalize_mac, paired_hubs,
                     parse_hub_lines, save_devices)

HUB = "00:07:80:4B:3F:25"


def test_mac_six_hex_pairs():
    assert normalize_mac(HUB) == HUB
    assert normalize_mac(" 00:07:80:4b:3f:25 ") == HUB
    assert normalize_mac("00-07-80-4B-3F-25") == HUB
    for bad in ("", "00:07:80:4B:3F", "00:07:80:4B:3F:25:01", "00:07:80:4B:3F:2G", "0007804B3F25",
                "00:07-80:4B:3F:25", "BTH00:07:80:4B:3F:25", None, 7):
        assert normalize_mac(bad) is None, bad


def test_old_devices_file_without_hub_means_not_set(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"plux": True, "flow": False}))
    assert load_devices(path) == {"plux": True, "flow": False, "hub": None}


def test_no_file_or_broken_file_gives_defaults(tmp_path):
    path = tmp_path / "devices.json"
    assert load_devices(path) == {"plux": True, "flow": True, "hub": None}
    path.write_text("{not json")
    assert load_devices(path) == {"plux": True, "flow": True, "hub": None}


def test_bad_hub_in_file_means_not_set(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"plux": True, "flow": True, "hub": "00:07:80"}))
    assert load_devices(path)["hub"] is None


def test_saving_one_setting_keeps_the_others(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"plux": True, "flow": False}))
    save_devices({"hub": HUB}, path)
    assert load_devices(path) == {"plux": True, "flow": False, "hub": HUB}
    save_devices({"flow": True}, path)  # the tick boxes must not forget the hub
    assert load_devices(path) == {"plux": True, "flow": True, "hub": HUB}


def test_find_hubs_keeps_biosignalsplux_over_bluetooth_only():
    found = (("BTH00:07:80:4B:3F:25", "biosignalsplux"),
             ("BTH00:07:80:3B:46:61", "BITalino"),
             ("USB0", "biosignalsplux"),
             {"path": "BTH00:07:80:aa:bb:cc", "description": "BiosignalsPlux"},
             ("BTH00:07:80:4B:3F:25", "biosignalsplux"))  # listed twice
    assert find_hubs(found) == [(HUB, "biosignalsplux"), ("00:07:80:AA:BB:CC", "BiosignalsPlux")]
    assert find_hubs(()) == []


def test_parse_find_output():
    text = f"{HUB}  biosignalsplux\nLooking for hubs ...\n00:07:80:AA:BB:CC  biosignalsplux\n\n"
    assert parse_hub_lines(text) == [(HUB, "biosignalsplux"), ("00:07:80:AA:BB:CC", "biosignalsplux")]
    assert parse_hub_lines("No biosignalsplux hub found") == []


def test_auto_hub_only_when_exactly_one():
    assert auto_hub([(HUB, "biosignalsplux")]) == HUB
    assert auto_hub([]) is None
    assert auto_hub([(HUB, "biosignalsplux"), ("00:07:80:AA:BB:CC", "biosignalsplux")]) is None


PNP_FIELD = r"BTHENUM\DEV_0007804D3382\8&2A1B&0&BLUETOOTHDEVICE_0007804D3382|True|biosignalsplux"
PNP_OLD = r"BTHENUM\DEV_0007804B3F25\A&C90331B&0&BLUETOOTHDEVICE_0007804B3F25|False|biosignalsplux"


def test_paired_hubs_from_windows_device_list():
    present, absent = paired_hubs("\n".join([PNP_FIELD, PNP_OLD, r"BTHENUM\DEV_28EA0BDAC484\x|True|Xbox",
                                             "garbage", ""]))
    assert present == [("00:07:80:4D:33:82", "biosignalsplux")]
    assert absent == [(HUB, "biosignalsplux")]
    assert paired_hubs("") == ([], [])


def test_candidates_prefer_present_pairings_over_find_devices():
    # 2026-11-27, CB-STIM-02: findDevices() returned () for a hub that was on and paired
    present, absent = paired_hubs(PNP_FIELD + "\n" + PNP_OLD)
    others = (("BTH00:07:80:AA:BB:CC", "biosignalsplux"),)
    assert candidate_hubs(present, absent, others) == [("00:07:80:4D:33:82", "biosignalsplux")]
    assert auto_hub(candidate_hubs(present, absent, ())) == "00:07:80:4D:33:82"


def test_candidates_without_a_present_pairing_fall_back_to_old_pairings_and_find_devices():
    _, absent = paired_hubs(PNP_OLD)
    found = (("BTH00:07:80:4D:33:82", "biosignalsplux"), ("BTH00:07:80:4B:3F:25", "biosignalsplux"))
    assert candidate_hubs([], absent, found) == [(HUB, "biosignalsplux"), ("00:07:80:4D:33:82", "biosignalsplux")]
    assert candidate_hubs([], [], ()) == []
