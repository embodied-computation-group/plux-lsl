"""Summarise flow (or another stream) in variable-length segments between start/end markers.

Usage (from the plux-lsl folder), e.g. the RRST wedge-level bench test:
    .venv\\Scripts\\python.exe segments.py bench.xdf --start "level_(\\w+)_ready" --end "level_{}_done"

--start is a regex with one group naming the segment; --end is the matching end
marker with {} where that name goes. For each segment it prints duration, breath
count and peak inspiratory flow per breath, and saves a CSV and a plot next to
the recording.
"""

import argparse
import csv
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy import signal as sps  # noqa: E402

from epoch import load  # noqa: E402
from flow_quality import MIN_BREATH_SLM  # noqa: E402


def breath_peaks(x, fs):
    sos = sps.butter(2, 5.0, "lowpass", fs=fs, output="sos")
    f = sps.sosfiltfilt(sos, x)
    peaks, _ = sps.find_peaks(f, height=MIN_BREATH_SLM, distance=int(1.0 * fs))
    return f, peaks


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xdf")
    ap.add_argument("--stream", default="Flow")
    ap.add_argument("--channel", default="flow")
    ap.add_argument("--start", required=True, help="regex with one group, e.g. 'level_(\\w+)_ready'")
    ap.add_argument("--end", required=True, help="end marker with {} for the name, e.g. 'level_{}_done'")
    args = ap.parse_args()

    path = Path(args.xdf)
    sig, markers = load(path, args.stream)
    info = sig["info"]
    fs = float(info["nominal_srate"][0])
    labels = [c["label"][0] for c in info["desc"][0]["channels"][0]["channel"]]
    col = labels.index(args.channel)
    ts = sig["time_stamps"]
    x = np.asarray(sig["time_series"], dtype=float)[:, col]

    start_re = re.compile(args.start)
    times = {v: t for t, v, _src in markers}  # last occurrence of each marker
    rows, traces = [], []
    for t0, v, _src in markers:
        m = start_re.fullmatch(v)
        if not m or times.get(v) != t0:
            continue
        name = m.group(1)
        t1 = times.get(args.end.format(name))
        if t1 is None or t1 <= t0:
            print(f"  {name}: no matching end marker, skipped")
            continue
        keep = (ts >= t0) & (ts <= t1)
        seg = x[keep]
        f, peaks = breath_peaks(seg, fs)
        pk = f[peaks]
        rows.append({"segment": name, "duration_s": round(t1 - t0, 2), "breaths": len(peaks),
                     "peak_median_slm": round(float(np.median(pk)), 2) if len(pk) else "",
                     "peak_min_slm": round(float(pk.min()), 2) if len(pk) else "",
                     "peak_max_slm": round(float(pk.max()), 2) if len(pk) else "",
                     "peaks_slm": " ".join(f"{p:.1f}" for p in pk)})
        traces.append((name, (ts[keep] - t0), f, peaks))
    if not rows:
        found = sorted({v for _t, v, _s in markers})
        raise SystemExit(f"No segments found. Marker strings present: {found[:30]}")

    print(f"{'segment':>10} {'dur s':>6} {'breaths':>7} {'median peak':>12}  peaks (slm)")
    for r in rows:
        print(f"{r['segment']:>10} {r['duration_s']:>6} {r['breaths']:>7} {str(r['peak_median_slm']):>12}  "
              f"{r['peaks_slm']}")

    stem = path.with_suffix("")
    out_csv = Path(f"{stem}_{args.stream}_segments.csv")
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7))
    cmap = plt.get_cmap("viridis", len(traces))
    for i, (name, t, f, peaks) in enumerate(traces):
        ax1.plot(t, f, color=cmap(i), lw=0.9, label=name)
        ax1.plot(t[peaks], f[peaks], "o", color=cmap(i), ms=4)
        ax2.plot([i] * len(peaks), f[peaks], "o", color=cmap(i), alpha=0.7)
    ax1.set_xlabel("seconds from segment start")
    ax1.set_ylabel(f"{args.channel} (slm)")
    ax1.legend(fontsize=8, ncol=min(len(traces), 6))
    ax1.grid(alpha=0.3)
    ax2.set_xticks(range(len(traces)), [n for n, *_ in traces])
    ax2.set_xlabel("segment")
    ax2.set_ylabel("peak inspiratory flow (slm)")
    ax2.grid(alpha=0.3)
    fig.suptitle(path.name, fontsize=9)
    fig.tight_layout()
    out_png = Path(f"{stem}_{args.stream}_segments.png")
    fig.savefig(out_png, dpi=110)
    print(f"Saved {out_csv}\nSaved {out_png}")


if __name__ == "__main__":
    main()
