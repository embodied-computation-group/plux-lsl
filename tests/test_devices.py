import json

import pytest

from devices import (DEFAULT_PORTS, auto_hub, candidate_hubs, describe_ports, find_hubs, format_port_kinds,
                     load_devices, normalize_mac, paired_hubs, parse_hub_lines, parse_port_kinds, plan_ports,
                     save_devices)

HUB = "00:07:80:4B:3F:25"
STANDARD = {1: "RESP", 2: "ECG"}  # belt on port 1, ECG on port 2


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
    assert load_devices(path) == {"plux": True, "flow": False, "hub": None, "ports": STANDARD}


def test_no_file_or_broken_file_gives_defaults(tmp_path):
    path = tmp_path / "devices.json"
    assert load_devices(path) == {"plux": True, "flow": True, "hub": None, "ports": STANDARD}
    path.write_text("{not json")
    assert load_devices(path) == {"plux": True, "flow": True, "hub": None, "ports": STANDARD}


def test_bad_hub_in_file_means_not_set(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"plux": True, "flow": True, "hub": "00:07:80"}))
    assert load_devices(path)["hub"] is None


def test_saving_one_setting_keeps_the_others(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"plux": True, "flow": False}))
    save_devices({"hub": HUB, "ports": STANDARD}, path)
    assert load_devices(path) == {"plux": True, "flow": False, "hub": HUB, "ports": STANDARD}
    save_devices({"flow": True}, path)  # the tick boxes must not forget the hub
    assert load_devices(path) == {"plux": True, "flow": True, "hub": HUB, "ports": STANDARD}


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


def test_port_map_in_devices_file(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"plux": True, "ports": {"3": "ecg", "1": "RESP", "x": "ECG", "4": "NOPE"}}))
    assert load_devices(path)["ports"] == {1: "RESP", 3: "ECG"}  # bad entries dropped
    path.write_text(json.dumps({"ports": {"4": "NOPE"}}))
    assert load_devices(path)["ports"] == DEFAULT_PORTS == STANDARD  # nothing usable: standard wiring
    save_devices({"hub": HUB}, path)
    assert json.loads(path.read_text())["ports"] == {"1": "RESP", "2": "ECG"}


def test_port_kinds_argument():
    assert parse_port_kinds("1=RESP,2=ECG") == STANDARD
    assert parse_port_kinds(" 2=ecg , 1=resp ") == STANDARD
    assert format_port_kinds(STANDARD) == "1=RESP,2=ECG"
    assert parse_port_kinds("") == {}
    for bad in ("1RESP", "1=RESP=2", "12=ECG", "1=HEART", "a=ECG"):
        with pytest.raises(ValueError):
            parse_port_kinds(bad)
    assert describe_ports(STANDARD) == "breathing belt (RESP) on port 1, ECG on port 2"


def test_chip_kinds_win():
    # (a) sensors with an ID chip keep the chip's kind, whatever the port map says
    assert plan_ports({1: "ECG", 2: "EDA"}, STANDARD) == ([(1, "ECG"), (2, "EDA")], False)


def test_ports_without_chip_info_take_the_mapped_kind():
    # (b) 2026-11-27, CB-STIM-02: chipless sensors come back as UNKNOWN with --ports 1,2,3,4
    chips = {1: "UNKNOWN", 2: "UNKNOWN", 3: "UNKNOWN", 4: "UNKNOWN"}
    assert plan_ports(chips, STANDARD, forced=[1, 2, 3, 4]) == (
        [(1, "RESP"), (2, "ECG"), (3, "UNKNOWN"), (4, "UNKNOWN")], False)
    assert plan_ports({}, STANDARD, forced=[2]) == ([(2, "ECG")], False)
    assert plan_ports({1: "UNKNOWN", 2: "ECG"}, {1: "RESP", 2: "RESP"}) == ([(1, "RESP"), (2, "ECG")], False)


def test_no_sensors_detected_falls_back_to_the_port_map():
    # (c) getSensors() returned nothing with a chipless sensor plugged in
    assert plan_ports({}, STANDARD) == ([(1, "RESP"), (2, "ECG")], True)
    assert plan_ports({}, {}) == ([], True)
