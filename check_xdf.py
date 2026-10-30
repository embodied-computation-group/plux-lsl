"""Summarise an XDF recording: streams, dropped samples, markers and signal quality.

Usage: .venv\\Scripts\\python.exe check_xdf.py recording.xdf
"""

import re
import sys

import numpy as np
import pyxdf

from flow_quality import check_flow
from rrst_markers import is_kids, kids_summary
from quality import BAD, OK, check_common, check_ecg, check_resp, to_units

SEGMENT_S = 10
GOOD_SHARE = 0.8  # a channel passes if at least this share of segments look good
FLOW_SEGMENT_S = 30  # RRST breaths are sparse; judge flow on longer stretches


def channel_quality(kind, raw, fs, bits):
    """Returns (share of good 10-s segments, rate text, {problem: count})."""
    seg = SEGMENT_S * fs
    n = len(raw) // seg
    if n == 0:
        return None, "", {}
    x, _ = to_units(kind, raw.astype(float), bits)
    good, problems = 0, {}
    for i in range(n):
        r, xs = raw[i * seg:(i + 1) * seg].astype(float), x[i * seg:(i + 1) * seg]
        res = check_common(r, bits)
        if res is None and kind == "ECG":
            res = check_ecg(xs, fs)
        if res is None and kind == "RESP":
            # breathing needs ~30 s to judge: use the 30 s ending here (or the first 30 s)
            stop = max((i + 1) * seg, 3 * seg)
            res = check_resp(x[max(0, stop - 3 * seg):stop], fs)
        if res is None or res[0] == OK:
            good += 1
        else:
            key = res[1].split(" - ")[0]
            problems[key] = problems.get(key, 0) + 1
    rate = ""
    if check_common(raw.astype(float), bits) is None:
        if kind == "RESP":
            colour, msg = check_resp(x, fs)
            rate = msg if colour == OK else ""
        elif kind == "ECG":
            colour, msg = check_ecg(x, fs)
            rate = msg.split(" - ")[0] if msg.startswith("HR") else ""
    return good / n, rate, problems


def rrst_trigger_check(values):
    """Every completed RRST trial sends two interval onsets and one response/noresponse."""
    onsets = sum(bool(re.fullmatch(r"trial_\d+_interval\d_onset_(load|null)", v)) for v in values)
    trials = sum(bool(re.fullmatch(r"trial_\d+_(response|noresponse)", v)) for v in values)
    ended = next((v for v in reversed(values) if v.startswith("session_end_")), None)
    slack = 0 if ended == "session_end_complete" else 2  # an aborted run may stop mid-trial
    ok = 0 <= onsets - 2 * trials <= slack and ended is not None
    text = f"RRST: {trials} trials, {onsets} onsets, {ended or 'no session_end marker'}"
    if not ok:
        text += " - triggers incomplete (expected 2 onsets per trial)" if ended else \
                " - task not finished when recording stopped?"
    return text, ok


def rrst_check(values, session_end):
    """[(text, ok)] for the RRST stream: the kids task (RRST-Kids) or the adult task."""
    if not values:
        return [("RRST: no task markers recorded - the task was not running while recording, "
                 "or was still waiting at its start prompt", False)]
    if is_kids(values):
        return kids_summary(values, session_end)
    return [rrst_trigger_check(values)]


def plux_quality(plux, start, end):
    """(passed, [(text, ok)]) for the PLUX stream inside the window."""
    info = plux["info"]
    fs = int(float(info["nominal_srate"][0]))
    bits = int(info["desc"][0]["acquisition"][0]["resolution_bits"][0])
    chans = info["desc"][0]["channels"][0]["channel"]
    labels, kinds = [c["label"][0] for c in chans], [c["type"][0] for c in chans]
    ts, data = plux["time_stamps"], np.asarray(plux["time_series"])
    dseq = np.diff(data[:, 0].astype(np.int64))
    dropped = int((dseq[dseq > 1] - 1).sum())
    if start is not None and end is not None:
        keep = (ts >= start) & (ts <= end)
        ts, data = ts[keep], data[keep]
    lines = [(f"Dropped samples: {dropped}" + ("" if dropped == 0 else
              " - keep the hub close to the laptop and charged"), dropped == 0)]
    ok = dropped == 0
    for i in range(1, len(labels)):
        share, rate, problems = channel_quality(kinds[i], data[:, i], fs, bits)
        if share is None:
            lines.append((f"{labels[i]}: too short to assess", False))
            ok = False
            continue
        passed = share >= GOOD_SHARE
        text = f"{labels[i]}: {share:.0%} good" + (f", {rate}" if rate else "")
        if problems:
            text += " - " + ", ".join(f"{k} ({v * SEGMENT_S} s)" for k, v in problems.items())
        lines.append((text, passed))
        ok &= passed
    return ok, lines


def flow_quality(flow, start, end):
    """(text, passed) for the Flow stream inside the window."""
    fs = int(round(float(flow["info"]["nominal_srate"][0])))
    ts, data = flow["time_stamps"], np.asarray(flow["time_series"], dtype=float)
    dseq = np.diff(data[:, 0].astype(np.int64))
    dropped = int((dseq[dseq > 1] - 1).sum())
    if start is not None and end is not None:
        keep = (ts >= start) & (ts <= end)
        covered = (ts[keep][-1] - ts[keep][0]) / (end - start) if keep.sum() > 1 else 0.0
        ts, data = ts[keep], data[keep]
    else:
        covered = 1.0
    if len(ts) < FLOW_SEGMENT_S * fs:
        return "Flow: too short to assess", False
    x = data[:, 1]
    seg = FLOW_SEGMENT_S * fs
    problems = {}
    for i in range(len(x) // seg):
        colour, msg = check_flow(x[i * seg:(i + 1) * seg], fs)
        key = msg.split(" - ")[0]
        if colour == BAD or key in ("REVERSE FLOW", "CONSTANT FLOW"):  # "no breaths" is normal between trials
            problems[key] = problems.get(key, 0) + 1
    colour, msg = check_flow(x, fs)
    passed = colour == OK and not problems and dropped == 0 and covered >= 0.95
    text = f"Flow: {msg}, dropped samples {dropped}"
    if covered < 0.95:
        text += f", covers only {covered:.0%} of the window (sensor lost?)"
    if problems:
        text += " - " + ", ".join(f"{k} ({v * FLOW_SEGMENT_S} s)" for k, v in problems.items())
    return text, passed


def assess(path, expect=("PLUX",)):
    """Quality report for one recording: {"ok": bool, "lines": [(text, ok_or_None)]}.

    expect: names of the device streams that must be present (e.g. ("PLUX", "Flow") for RRST).
    """
    streams, _ = pyxdf.load_xdf(str(path))
    lines = []
    by_name, session = {}, {}
    rrst = None  # (position in lines, RRST marker values)
    for s in streams:
        info = s["info"]
        name = info["name"][0]
        by_name[name] = s
        if info["type"][0] != "Markers":
            continue
        values = [v[0] for v in s["time_series"]]
        if name == "SessionMarkers":
            for t, v in zip(s["time_stamps"], values):
                session.setdefault(v, t)
        else:
            onsets = sum("_onset" in v for v in values)
            what = "slurps" if name == "RRST" and is_kids(values) else "onsets"
            lines.append((f"{name} triggers: {len(values)}" + (f" ({onsets} {what})" if onsets else ""),
                          len(values) > 0))
            if name == "RRST":
                rrst = (len(lines), values)
    start = next((t for m, t in session.items() if m.endswith("_start")), None)
    end_label = next((m for m in session if m.endswith(("_end", "_end_early"))), None)
    if rrst is not None:
        lines[rrst[0]:rrst[0]] = rrst_check(rrst[1], end_label)
    ok = all(good is not False for _t, good in lines)
    end = session[end_label] if end_label else None
    if start is not None and end is not None:
        early = end_label.endswith("_end_early")
        lines.insert(0, (f"Window: {(end - start) / 60:.1f} min" + (" (stopped early)" if early else ""),
                         not early))
        ok &= not early
    else:
        lines.insert(0, ("No start/end markers - window length unknown", False))
        ok = False

    plux = by_name.get("PLUX")
    if plux is not None:
        plux_ok, plux_lines = plux_quality(plux, start, end)
        lines += plux_lines
        ok &= plux_ok

    if "Flow" in by_name:
        text, passed = flow_quality(by_name["Flow"], start, end)
        lines.append((text, passed))
        ok &= passed
    for name in expect:
        if name not in by_name:
            lines.append((f"{name}: MISSING from the recording - was the device connected?", False))
            ok = False
    return {"ok": ok, "lines": lines}


def summarize(path):
    report = assess(path)
    print("PASS" if report["ok"] else "CHECK - see lines marked !")
    for text, good in report["lines"]:
        print(f"  {'!' if good is False else ' '} {text}")
    return report


if __name__ == "__main__":
    summarize(sys.argv[1])
