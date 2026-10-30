"""Standard figures for an RRST recording (or a wedge-level bench recording).

Usage (from the plux-lsl folder):
    .venv\\Scripts\\python.exe rrst_figures.py data\\sub-01\\ses-1\\sub-01_ses-1_task-rrst_physio.xdf
    .venv\\Scripts\\python.exe rrst_figures.py <file>.xdf --task-csv <RRST task .csv>   # recordings before 2026-10-29
    add --open to open the figures folder afterwards

Writes PNGs into <file>_figures\\ next to the recording (or --out <folder>):
  RRST session (markers trial_<n>_interval<k>_onset_load|null):
    1_session.png   belt and flow over the whole session, inhale cues marked
    2_aligned.png   every breath aligned on its inhale cue, null vs load (belt, flow)
    3_by_trial.png  staircase level per trial; null and load peak flow per trial
    4_breaths.png   one panel per trial: its null and load flow curves overlaid
  RRST-Kids session (markers trial_<n>_level_<x>, trial_<n>_go, slurp_<k>_..., see rrst_markers.py):
    1_session.png   flow (and belt) over the whole session, every slurp onset and trial marked
    2_trials.png    one panel per trial: its slurps aligned on trial_<n>_go; level and answer in the title
    3_slurps.png    volume and peak of every counted slurp by level (free play and catch trials apart)
  Bench (bench/flowLevels.m, markers level_<x>_ready / _done):
    levels.png      flow at each wedge level: trace, breaths, peak, volume, time to peak

Level and correctness come from the trial_<n>_level_<x> and trial_<n>_correct|wrong
markers; older recordings need the task's .csv (--task-csv). Where a trial was
repeated after no response, the last attempt is used. Belt panels need a RESP channel
in the PLUX stream and are left out without one.
"""

import argparse
import csv
import os
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pyxdf  # noqa: E402
from scipy import signal as sps  # noqa: E402

from quality import to_units  # noqa: E402
from rrst_markers import is_kids, parse_kids  # noqa: E402

INK, MUTED, GRID, SURF = "#1f1f1e", "#6b6a66", "#e6e5e1", "#fcfcfb"
NULL, LOAD = "#2a78d6", "#eb6834"                                      # categorical slots 1 and 2
LEVEL_RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"]   # ordinal blue
MIN_BREATH = 10        # slm; wedge moves give blips under 5
PRE, POST = 1.0, 3.0   # s around the inhale cue; breaths can start up to ~1 s early

plt.rcParams.update({"font.family": ["Segoe UI", "Segoe UI Symbol"], "font.size": 10, "axes.edgecolor": MUTED,
                     "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "savefig.facecolor": SURF, "figure.facecolor": SURF})


def labels(stream):
    return [c["label"][0] for c in stream["info"]["desc"][0]["channels"][0]["channel"]]


def lowpass(x, fs, hz):
    return sps.sosfiltfilt(sps.butter(2, hz, "lowpass", fs=fs, output="sos"), x)


def load(path):
    streams, _ = pyxdf.load_xdf(str(path))
    by = {s["info"]["name"][0]: s for s in streams}
    flow = None
    if "Flow" in by:
        f = by["Flow"]
        tf = f["time_stamps"]
        fsf = 1 / np.median(np.diff(tf))
        flow = (tf, lowpass(f["time_series"][:, labels(f).index("flow")], fsf, 10), fsf)

    belt = None
    if "PLUX" in by:
        p = by["PLUX"]
        resp = [i for i, lab in enumerate(labels(p)) if lab.startswith("RESP")]
        if resp:
            bits = int(p["info"]["desc"][0]["acquisition"][0]["resolution_bits"][0])
            x, _ = to_units("RESP", p["time_series"][:, resp[0]].astype(float), bits)
            tb = p["time_stamps"]
            fsb = 1 / np.median(np.diff(tb))
            belt = (tb, lowpass(x, fsb, 2), fsb)

    last = {}   # last occurrence of each marker: a repeated trial keeps its final attempt
    ordered = []  # every task marker, (time, label), in time order
    for s in streams:
        if s["info"]["type"][0] == "Markers" and s["info"]["name"][0] != "SessionMarkers":
            for m, ts in zip(s["time_series"], s["time_stamps"]):
                last[m[0]] = ts
                ordered.append((ts, m[0]))
    ordered.sort(key=lambda x: x[0])
    return flow, belt, last, ordered


def save(fig, out, name):
    fig.savefig(out / name, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print("  ", out / name)


# --- RRST -------------------------------------------------------------------------

def trials_from(last, task_csv):
    trials = {}
    for m, ts in last.items():
        g = re.fullmatch(r"trial_(\d+)_interval(\d)_onset_(load|null)", m)
        if g:
            trials.setdefault(int(g[1]), {})[g[3]] = ts
        g = re.fullmatch(r"trial_(\d+)_level_(.+)", m)
        if g:
            trials.setdefault(int(g[1]), {})["level"] = float(g[2])
        g = re.fullmatch(r"trial_(\d+)_(correct|wrong)", m)
        if g:
            trials.setdefault(int(g[1]), {})["correct"] = g[2] == "correct"
    if task_csv:
        for r in csv.DictReader(open(task_csv)):
            if r["trialN"] in ("", "NaN"):
                continue
            t = trials.setdefault(int(float(r["trialN"])), {})
            t.setdefault("level", float(r["Stim"]))
            if r["Resp"] not in ("", "NaN"):
                t.setdefault("correct", float(r["Resp"]) == 1)
    return {n: t for n, t in sorted(trials.items()) if "load" in t and "null" in t}


def breath(sig, t0):
    """Peak flow (slm) and inhaled volume (L) of the breath taken for the cue at t0."""
    t, x, _ = sig
    s = np.flatnonzero((t >= t0 - PRE) & (t <= t0 + POST))
    if not len(s) or x[s].max() < MIN_BREATH:
        return np.nan, np.nan
    p = s[np.argmax(x[s])]
    lo, hi = p, p
    while lo > 0 and x[lo] > 0.5:
        lo -= 1
    while hi < len(x) - 1 and x[hi] > 0.5:
        hi += 1
    return x[p], np.trapezoid(x[lo:hi], t[lo:hi]) / 60


def window(sig, t0, baseline=False):
    t, x, fs = sig
    n = int((PRE + POST) * fs)
    i = np.searchsorted(t, t0 - PRE)
    if i + n > len(x):
        return None, None
    e = x[i:i + n].copy()
    if baseline:
        e -= e[: int(PRE * fs)].mean()
    return np.arange(n) / fs - PRE, e


def fig_session(flow, belt, last, trials, out):
    onsets = sorted((t[k], k, n) for n, t in trials.items() for k in ("null", "load"))
    t0 = last.get("session_start", onsets[0][0] - 5)
    t1 = max(v for k, v in last.items() if k.startswith("session_end")) if any(
        k.startswith("session_end") for k in last) else onsets[-1][0] + 15
    rows = [(sig, lab) for sig, lab in ((belt, "belt (%)"), (flow, "flow (slm)")) if sig]
    fig, axes = plt.subplots(len(rows), 1, figsize=(15, 3.2 * len(rows) + 0.6), sharex=True, squeeze=False)
    for ax, (sig, ylab) in zip(axes[:, 0], rows):
        t, x, _ = sig
        s = (t >= t0 - 5) & (t <= t1 + 5)
        for ts, kind, _ in onsets:
            ax.axvline(ts - t0, color=NULL if kind == "null" else LOAD, lw=0.9, alpha=0.55, zorder=0)
        ax.plot(t[s] - t0, x[s], color=INK, lw=0.8)
        ax.set_ylabel(ylab)
        ax.grid(axis="y", color=GRID, lw=0.8)
    top = axes[0, 0]
    for n, tr in trials.items():
        top.text(min(tr["null"], tr["load"]) - t0, 1.01, str(n), transform=top.get_xaxis_transform(),
                 fontsize=7, color=MUTED, va="bottom")
    top.set_title("Whole session. Vertical lines: inhale cues (blue null, orange load); numbers: trials",
                  loc="left", color=INK, pad=14)
    axes[-1, 0].set_xlabel("time from session start (s)")
    save(fig, out, "1_session.png")


def fig_aligned(flow, belt, trials, out):
    rows = [r for r in ((belt, True, "belt change (%)"), (flow, False, "flow (slm)")) if r[0]]
    fig, axes = plt.subplots(len(rows), 2, figsize=(13, 3.6 * len(rows) + 0.6), sharex=True, squeeze=False)
    for r, (sig, base, ylab) in enumerate(rows):
        for c, kind in enumerate(("null", "load")):
            ax = axes[r, c]
            ep = []
            for tr in trials.values():
                tt, e = window(sig, tr[kind], base)
                if e is not None:
                    ep.append(e)
                    ax.plot(tt, e, color=NULL if kind == "null" else LOAD, lw=0.8, alpha=0.35)
            if ep:
                ax.plot(tt, np.mean(ep, 0), color=INK, lw=2)
            ax.axvline(0, color=MUTED, lw=1, ls="--")
            ax.grid(axis="y", color=GRID, lw=0.8)
            ax.set_title(f"{kind}  (n={len(ep)})", color=INK)
            if c == 0:
                ax.set_ylabel(ylab)
        lim = (min(a.get_ylim()[0] for a in axes[r]), max(a.get_ylim()[1] for a in axes[r]))
        for a in axes[r]:
            a.set_ylim(lim)
    for a in axes[-1]:
        a.set_xlabel("s from inhale cue")
    fig.suptitle("Every breath aligned on its inhale cue (black = mean)", x=0.01, ha="left", color=INK)
    fig.tight_layout()
    save(fig, out, "2_aligned.png")


def fig_by_trial(flow, trials, out):
    n = np.array(list(trials))
    lev = np.array([trials[k].get("level", np.nan) for k in n])
    cor = np.array([trials[k].get("correct", np.nan) for k in n], dtype=float)
    pn, vn = np.array([breath(flow, trials[k]["null"]) for k in n]).T
    pl, vl = np.array([breath(flow, trials[k]["load"]) for k in n]).T
    have_level = np.isfinite(lev).any()

    ratios = ([1] if have_level else []) + [1.4, 1.4]
    fig, axes = plt.subplots(len(ratios), 1, figsize=(max(8, 0.45 * len(n) + 2), 3 * len(ratios) + 0.5),
                             sharex=True, squeeze=False, gridspec_kw={"height_ratios": ratios})
    if have_level:
        a1 = axes[0, 0]
        a1.plot(n, lev, color=MUTED, lw=1.5, zorder=1)
        ok, bad = cor == 1, cor == 0
        a1.scatter(n[ok], lev[ok], s=60, color=INK, edgecolor=SURF, lw=2, zorder=3, label="correct")
        a1.scatter(n[bad], lev[bad], s=60, facecolor=SURF, edgecolor=INK, lw=1.8, zorder=3, label="wrong")
        a1.set_ylabel("load level")
        a1.grid(axis="y", color=GRID, lw=0.8)
        a1.legend(frameon=False, loc="lower right", ncol=2)
        a1.set_title("Staircase level on each trial, and the peak flow and volume of its null and load breath",
                     loc="left", color=INK)
    for ax, a_, b_, ylab, pad in ((axes[-2, 0], pn, pl, "peak flow (slm)", 6),
                                  (axes[-1, 0], vn, vl, "inhaled volume (L)", 0.15)):
        for k, a, b in zip(n, a_, b_):
            ax.plot([k, k], [a, b], color=MUTED, lw=1.5, zorder=1)
        ax.scatter(n, a_, s=60, color=NULL, edgecolor=SURF, lw=2, zorder=3, label="null breath")
        ax.scatter(n, b_, s=60, color=LOAD, edgecolor=SURF, lw=2, zorder=3, label="load breath")
        ax.legend(frameon=False, loc="upper left", ncol=2, fontsize=9)
        ax.set_ylim(np.nanmin([a_, b_]) - pad, np.nanmax([a_, b_]) + 3 * pad)   # room for the legend
        ax.set_ylabel(ylab)
        ax.grid(axis="y", color=GRID, lw=0.8)
    a2 = axes[-1, 0]
    a2.set_xlim(n[0] - 0.6, n[-1] + 0.6)
    a2.set_xlabel("trial")
    a2.set_xticks(n if len(n) <= 40 else n[::5])
    fig.text(0.01, -0.01, f"Load breath lower: peak flow on {np.sum(pl < pn)} of {len(n)} trials "
             f"(median null minus load {np.nanmedian(pn - pl):.0f} slm); volume on {np.sum(vl < vn)} "
             f"of {len(n)} (median {np.nanmedian(vn - vl):.2f} L).", color=MUTED, fontsize=9.5, va="top")
    save(fig, out, "3_by_trial.png")
    return n, lev, cor, pn, pl, vn, vl


def fig_breaths(flow, trials, out):
    n = list(trials)
    cols = min(10, len(n))
    rows = int(np.ceil(len(n) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(1.7 * cols + 0.6, 1.75 * rows + 0.8),
                             sharex=True, sharey=True, squeeze=False)
    for ax in axes.flat[len(n):]:
        ax.axis("off")
    for ax, k in zip(axes.flat, n):
        tr = trials[k]
        for kind, col in (("null", NULL), ("load", LOAD)):
            tt, e = window(flow, tr[kind])
            if e is not None:
                ax.plot(tt, e, color=col, lw=1.6)
        ax.axvline(0, color=MUTED, lw=0.8, ls="--")
        ax.grid(axis="y", color=GRID, lw=0.6)
        lev = tr.get("level")
        mark = {True: "  ✓", False: "  ✗"}.get(tr.get("correct"), "")
        ax.set_title(f"{k}" + (f" · {lev:g}" if lev is not None else "") + mark, fontsize=9, color=INK)
        ax.tick_params(labelsize=8)
    for ax in axes[:, 0]:
        ax.set_ylabel("slm", fontsize=8)
    for ax in axes[-1]:
        ax.set_xlabel("s", fontsize=8)
    fig.suptitle("Each trial's null (blue) and load (orange) breath, aligned on the inhale cue. "
                 "Title: trial · load level, ✓ correct ✗ wrong", x=0.01, ha="left", color=INK, fontsize=10)
    fig.tight_layout()
    save(fig, out, "4_breaths.png")


# --- RRST-Kids ------------------------------------------------------------------------

def kids_group(tr):
    """Colour group of a kids trial: free play / free trial, catch (level 0) or load."""
    if tr["free"]:
        return "free"
    return "catch" if not tr["level"] else "load"


KIDS_COLOUR = {"free": MUTED, "catch": NULL, "load": LOAD}


def kids_title(tr):
    """'4 · 80 · yes ✓', 'demo pinch · 80', '1 · 0 · free'."""
    head = f"demo {tr['demo']}" if tr["phase"] == "demo" else str(tr["n"])
    parts = [head] + ([f"{tr['level']:g}"] if tr["level"] is not None else [])
    if tr["free"]:
        parts.append("free")
    elif tr["answer"]:
        parts.append(tr["answer"] + {True: " ✓", False: " ✗"}.get(tr["correct"], ""))
    elif tr.get("aborted"):
        parts.append("aborted")
    return " · ".join(parts)


def fig_kids_session(flow, belt, ordered, kids, out):
    t0 = ordered[0][0]
    t1 = ordered[-1][0] + 10
    rows = [(sig, lab) for sig, lab in ((belt, "belt (%)"), (flow, "flow (slm)")) if sig]
    fig, axes = plt.subplots(len(rows), 1, figsize=(15, 3.2 * len(rows) + 0.8), sharex=True, squeeze=False)
    for ax, (sig, ylab) in zip(axes[:, 0], rows):
        t, x, _ = sig
        sel = (t >= t0 - 5) & (t <= t1)
        for tr in kids["trials"]:
            col = KIDS_COLOUR[kids_group(tr)]
            for sl in tr["slurps"]:
                if sl["onset"] is not None:
                    ax.axvline(sl["onset"] - t0, color=col, lw=0.9, alpha=0.6, zorder=0)
            if tr["t"] is not None and tr["phase"] != "freeplay":
                ax.axvline(tr["t"] - t0, color=MUTED, lw=0.8, ls=":", zorder=0)
        for ts, label in ordered:
            if label in ("practice_start", "main_start", "rest_start", "aborted", "done"):
                ax.axvline(ts - t0, color=INK, lw=1.2, ls="--", zorder=0)
        ax.plot(t[sel] - t0, x[sel], color=INK, lw=0.8)
        ax.set_ylabel(ylab)
        ax.grid(axis="y", color=GRID, lw=0.8)
    top = axes[0, 0]
    for tr in kids["trials"]:
        if tr["t"] is not None and tr["phase"] != "freeplay":
            text = f"d{tr['demo'][0]}" if tr["phase"] == "demo" else str(tr["n"])
            top.text(tr["t"] - t0, 1.01, f"{text}·{tr['level']:g}" if tr["level"] is not None else text,
                     transform=top.get_xaxis_transform(), fontsize=7, color=MUTED, va="bottom", rotation=60)
    for ts, label in ordered:
        if label in ("practice_start", "main_start", "rest_start", "aborted", "done"):
            top.text(ts - t0 + 1, 0.97, label.replace("_start", ""), transform=top.get_xaxis_transform(),
                     fontsize=8, color=INK, va="top", ha="left",
                     bbox=dict(facecolor=SURF, edgecolor="none", pad=1.5))
    top.set_title("RRST-Kids: whole session. Coloured lines: slurp onsets (blue catch, orange load, grey free); "
                  "dotted: trial start, labelled trial·level", loc="left", color=INK, pad=34)
    axes[-1, 0].set_xlabel("time from the first task marker (s)")
    save(fig, out, "1_session.png")


def fig_kids_trials(flow, kids, out):
    trials = [tr for tr in kids["trials"] if tr["go"] is not None]
    t, x, _ = flow
    ends = [max((sl["end"] or sl["onset"] or tr["go"]) for sl in tr["slurps"]) - tr["go"]
            for tr in trials if tr["slurps"]]
    post = min(25.0, (max(ends) if ends else 10.0) + 1.5)
    cols = min(8, len(trials))
    rows = int(np.ceil(len(trials) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(2.0 * cols + 0.6, 1.8 * rows + 0.9),
                             sharex=True, sharey=True, squeeze=False)
    for ax in axes.flat[len(trials):]:
        ax.axis("off")
    for ax, tr in zip(axes.flat, trials):
        sel = (t >= tr["go"] - 1) & (t <= tr["go"] + post)
        ax.plot(t[sel] - tr["go"], x[sel], color=KIDS_COLOUR[kids_group(tr)], lw=1.4)
        for sl in tr["slurps"]:
            if sl["onset"] is not None:
                ax.axvline(sl["onset"] - tr["go"], color=MUTED, lw=0.7, ls=":")
            if sl["status"] in ("weak", "overlong") and sl["end"] is not None:
                ax.text(sl["end"] - tr["go"], 0.95, "weak" if sl["status"] == "weak" else "long",
                        transform=ax.get_xaxis_transform(), fontsize=7, color=MUTED, ha="center")
        ax.axvline(0, color=INK, lw=0.8, ls="--")
        ax.grid(axis="y", color=GRID, lw=0.6)
        ax.set_title(kids_title(tr), fontsize=9, color=INK)
        ax.tick_params(labelsize=8)
    for ax in axes[:, 0]:
        ax.set_ylabel("slm", fontsize=8)
    for ax in axes[-1]:
        ax.set_xlabel("s from go", fontsize=8)
    fig.suptitle("Each trial's slurps, aligned on the experimenter's go (dashed); dotted: slurp onsets. "
                 "Title: trial · level · answer (✓ correct, ✗ wrong). Blue catch, orange load, grey free",
                 x=0.01, ha="left", color=INK, fontsize=10)
    fig.tight_layout()
    save(fig, out, "2_trials.png")


def fig_kids_slurps(kids, out):
    pts = []  # (group key, main?, volume, peak)
    for tr in kids["trials"]:
        key = "free" if tr["free"] else tr["level"]
        for sl in tr["slurps"]:
            if sl["status"] == "counted" and key is not None:
                pts.append((key, tr["phase"] == "main", sl["volume"], sl["peak"]))
    if not pts:
        print("   (no counted slurps: slurp figure left out)")
        return
    levels = sorted({k for k, *_ in pts if k != "free"})
    keys = (["free"] if any(k == "free" for k, *_ in pts) else []) + levels
    names = {k: "free" if k == "free" else "0 (catch)" if k == 0 else f"{k:g}" for k in keys}
    fig, axes = plt.subplots(1, 2, figsize=(max(8, 1.6 * len(keys) * 2 + 2), 4.2))
    for ax, j, ylab in ((axes[0], 2, "volume (L)"), (axes[1], 3, "peak flow (slm)")):
        for i, k in enumerate(keys):
            col = KIDS_COLOUR["free" if k == "free" else "catch" if k == 0 else "load"]
            vals = [(p[1], p[j]) for p in pts if p[0] == k and p[j] is not None]
            jit = np.linspace(-0.22, 0.22, len(vals)) if len(vals) > 1 else [0.0]
            for dx, (main, v) in zip(jit, vals):
                ax.scatter(i + dx, v, s=42, zorder=3, lw=1.6, edgecolor=col if not main else SURF,
                           facecolor=col if main else SURF)
            if vals:
                ax.plot([i - 0.3, i + 0.3], [np.median([v for _, v in vals])] * 2, color=INK, lw=2, zorder=4)
                ax.text(i, 1.0, f"n={len(vals)}", transform=ax.get_xaxis_transform(), ha="center",
                        va="bottom", fontsize=8, color=MUTED)
        if 0 in keys and len(levels) > 1:
            ax.axvline(keys.index(0) + 0.5, color=GRID, lw=1.5)
        ax.set_xticks(range(len(keys)), [names[k] for k in keys])
        ax.set_xlim(-0.6, len(keys) - 0.4)
        ax.set_ylim(bottom=0)
        ax.set_xlabel("level")
        ax.set_ylabel(ylab)
        ax.grid(axis="y", color=GRID, lw=0.8)
    fig.suptitle("Every counted slurp by level (the task's own volume and peak); bar = median. "
                 "Filled: main trials; hollow: practice, demos, free play", x=0.01, ha="left", color=INK,
                 fontsize=10)
    fig.tight_layout()
    save(fig, out, "3_slurps.png")


def kids_figures(flow, belt, ordered, out):
    kids = parse_kids([v for _, v in ordered], [t for t, _ in ordered])
    if kids["end"] == "aborted" and kids["trials"] and kids["trials"][-1]["answer"] is None:
        kids["trials"][-1]["aborted"] = True
    n_tr = sum(tr["phase"] in ("practice", "main") and not tr["free"] for tr in kids["trials"])
    n_sl = sum(len(tr["slurps"]) for tr in kids["trials"])
    print(f"   RRST-Kids: {n_tr} trials, {n_sl} slurps, end: {kids['end'] or 'none'}")
    if not belt:
        print("   (no breathing belt: belt panel left out)")
    fig_kids_session(flow, belt, ordered, kids, out)
    if not flow:
        print("   (no Flow stream: trial and slurp figures left out)")
    elif any(tr["go"] is not None for tr in kids["trials"]):
        fig_kids_trials(flow, kids, out)
    else:
        print("   (no trial_<n>_go markers: trial figure left out)")
    fig_kids_slurps(kids, out)


# --- bench: wedge levels -------------------------------------------------------------

def fig_levels(flow, last, out):
    t, f, fs = flow
    levels = sorted({float(re.fullmatch(r"level_(.+)_ready", k)[1]) for k in last
                     if re.fullmatch(r"level_.+_ready", k)})
    col = {lv: LEVEL_RAMP[min(i * len(LEVEL_RAMP) // len(levels), len(LEVEL_RAMP) - 1)]
           for i, lv in enumerate(levels)}
    res = {}
    for lv in levels:
        a, b = last[f"level_{lv:g}_ready"], last.get(f"level_{lv:g}_done", t[-1])
        sel = (t >= a) & (t <= b)
        seg, ts = f[sel], t[sel]
        pk, _ = sps.find_peaks(seg, height=MIN_BREATH, distance=int(fs))
        br = []
        for p in pk:
            lo = p
            while lo > 0 and seg[lo] > 0.5:
                lo -= 1
            hi = p
            while hi < len(seg) - 1 and seg[hi] > 0.5:
                hi += 1
            br.append(dict(onset=ts[lo], peak=seg[p], vol=np.trapezoid(seg[lo:hi], ts[lo:hi]) / 60,
                           tpk=ts[p] - ts[lo]))
        res[lv] = dict(a=a, b=b, breaths=br)

    fig = plt.figure(figsize=(14, 10.5))
    gs = fig.add_gridspec(3, len(levels), height_ratios=[1.1, 1, 1], hspace=0.55, wspace=0.28)
    ax = fig.add_subplot(gs[0, :])
    t0 = res[levels[0]]["a"] - 3
    s = (t >= t0) & (t <= res[levels[-1]]["b"] + 3)
    ax.plot(t[s] - t0, f[s], color=INK, lw=1)
    for lv in levels:
        r = res[lv]
        ax.axvspan(r["a"] - t0, r["b"] - t0, color=col[lv], alpha=0.18, lw=0)
        ax.text((r["a"] + r["b"]) / 2 - t0, 1.02, f"{lv:g} %", transform=ax.get_xaxis_transform(),
                ha="center", va="bottom", color=INK, fontweight="bold")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("flow (slm)")
    ax.set_title("A  Flow with the wedge held at each level (shaded)", loc="left", color=INK, pad=22)

    ymax = max([b["peak"] for r in res.values() for b in r["breaths"]] + [10]) * 1.1
    for i, lv in enumerate(levels):
        ax = fig.add_subplot(gs[1, i])
        for b in res[lv]["breaths"]:
            s = (t >= b["onset"] - 0.3) & (t <= b["onset"] + 2.2)
            ax.plot(t[s] - b["onset"], f[s], color=col[lv], lw=2)
        ax.set_ylim(-1, ymax)
        ax.set_xlim(-0.3, 2.2)
        ax.set_title(f"{lv:g} %  (n={len(res[lv]['breaths'])})", color=INK)
        ax.set_xlabel("s from breath onset")
        if i == 0:
            ax.set_ylabel("flow (slm)")
            ax.text(0, 1.22, "B  Each breath, aligned at onset", transform=ax.transAxes, color=INK, fontsize=11)
        else:
            ax.tick_params(labelleft=False)

    spans = np.array_split(np.arange(len(levels)), 3) if len(levels) >= 3 else [np.arange(len(levels))]
    for j, (key, ylab, title) in enumerate([("peak", "peak flow (slm)", "C  Peak flow"),
                                             ("vol", "volume (L)", "D  Volume"),
                                             ("tpk", "time to peak (s)", "E  Onset to peak")][:len(spans)]):
        ax = fig.add_subplot(gs[2, spans[j][0]:spans[j][-1] + 1])
        for lv in levels:
            v = [b[key] for b in res[lv]["breaths"]]
            jit = np.linspace(-1.2, 1.2, len(v)) if len(v) > 1 else [0]
            ax.scatter(np.array(jit) + lv, v, s=40, color=col[lv], edgecolor=SURF, lw=1.5, zorder=3)
            if v:
                ax.plot([lv - 3, lv + 3], [np.median(v)] * 2, color=INK, lw=2, zorder=4)
        ax.set_xticks(levels)
        ax.set_xlabel("wedge level (%)")
        ax.set_ylabel(ylab)
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", color=GRID, lw=0.8)
        ax.set_title(title + ("  (dots = breaths, bar = median)" if j == 0 else ""), loc="left", color=INK)
    save(fig, out, "levels.png")
    for lv in levels:
        br = res[lv]["breaths"]
        if br:
            print(f"   {lv:5g} %: {len(br)} breaths, peak {np.median([b['peak'] for b in br]):.0f} slm, "
                  f"volume {np.median([b['vol'] for b in br]):.2f} L")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xdf")
    ap.add_argument("--task-csv", help="RRST task .csv, for recordings without trial_<n>_level markers")
    ap.add_argument("--open", action="store_true", help="open the figures folder afterwards")
    ap.add_argument("--out", help="folder for the figures (default: <file>_figures next to the recording)")
    args = ap.parse_args()

    path = Path(args.xdf)
    out = Path(args.out) if args.out else path.parent / f"{path.stem}_figures"
    out.mkdir(parents=True, exist_ok=True)
    flow, belt, last, ordered = load(path)
    print(f"Figures for {path.name}:")
    if not flow and not belt:
        print("   No Flow stream and no breathing belt in this recording: no figures.")
        return
    if any(re.fullmatch(r"level_.+_ready", k) for k in last):
        if flow:
            fig_levels(flow, last, out)
        else:
            print("   Bench recording without Flow: no figures.")
        return
    if ordered and is_kids([v for _, v in ordered]):
        kids_figures(flow, belt, ordered, out)
        if args.open:
            os.startfile(out)
        return
    trials = trials_from(last, args.task_csv)
    if not trials:
        print("   No complete RRST trials in this recording: no figures.")
        return
    if not belt:
        print("   (no breathing belt: belt panels left out)")
    fig_session(flow, belt, last, trials, out)
    fig_aligned(flow, belt, trials, out)
    if not flow:
        print("   (no Flow stream: by-trial and breath figures left out)")
        return
    _, lev, *_ = fig_by_trial(flow, trials, out)
    fig_breaths(flow, trials, out)
    if not np.isfinite(lev).any():
        print("   (no levels: pass --task-csv for recordings made before the level markers)")
    if args.open:
        os.startfile(out)


if __name__ == "__main__":
    main()
