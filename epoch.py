"""Cut a recording into epochs around task markers and plot every trial plus the average.

Usage (from the plux-lsl folder):
    .venv\\Scripts\\python.exe epoch.py data\\sub-01\\ses-1\\sub-01_ses-1_task-rrst_physio.xdf ^
        --stream Flow --events "interval.*onset" --pre 1 --post 5

    --stream   LSL stream to epoch (Flow, PLUX)
    --events   regular expression matched against marker strings (all marker streams)
    --channels channel labels to keep (default: all except nSeq)
    --groups   separate averages per substring of the marker, e.g. --groups load null

RRST example (flow around each inhale cue, load vs null):
    epoch.py <file>.xdf --stream Flow --events "interval\\d_onset_" --pre 1 --post 3 --groups load null

Writes <file>_<stream>_epochs.npz (data: events x channels x samples, times, labels,
event names) and a PNG next to the recording, and prints the number of epochs found.
"""

import argparse
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pyxdf  # noqa: E402

from quality import to_units  # noqa: E402


def load(path, stream_name):
    streams, _ = pyxdf.load_xdf(str(path))
    signal, markers = None, []
    for s in streams:
        info = s["info"]
        if info["name"][0] == stream_name:
            signal = s
        elif info["type"][0] == "Markers":
            markers += [(t, v[0], info["name"][0]) for t, v in zip(s["time_stamps"], s["time_series"])]
    if signal is None:
        names = ", ".join(s["info"]["name"][0] for s in streams)
        raise SystemExit(f"No stream named {stream_name!r} in {path} (found: {names})")
    return signal, sorted(markers)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xdf")
    ap.add_argument("--stream", default="Flow")
    ap.add_argument("--events", required=True, help="regex for marker strings, e.g. 'interval.*onset'")
    ap.add_argument("--pre", type=float, default=1.0, help="seconds before each event")
    ap.add_argument("--post", type=float, default=5.0, help="seconds after each event")
    ap.add_argument("--channels", nargs="*", help="channel labels to keep")
    ap.add_argument("--groups", nargs="*", help="plot a separate mean per substring, e.g. --groups load null")
    args = ap.parse_args()

    path = Path(args.xdf)
    sig, markers = load(path, args.stream)
    info = sig["info"]
    fs = float(info["nominal_srate"][0])
    chans = info["desc"][0]["channels"][0]["channel"]
    labels = [c["label"][0] for c in chans]
    kinds = [c["type"][0] for c in chans]
    units = [c["unit"][0] for c in chans]
    keep = [i for i, lab in enumerate(labels)
            if lab != "nSeq" and (not args.channels or lab in args.channels)]
    ts = sig["time_stamps"]
    data = np.asarray(sig["time_series"], dtype=float)

    # PLUX streams raw ADC counts; convert to physical units for plotting.
    bits = info["desc"][0].get("acquisition", [{}])[0].get("resolution_bits", [None])[0]
    if bits is not None:
        for i in keep:
            data[:, i], units[i] = to_units(kinds[i], data[:, i], int(bits))

    pattern = re.compile(args.events)
    # A repeated trial (RRST repeats no-response trials under the same number) reuses
    # its marker string; keep the last occurrence of each.
    last = {v: t for t, v, _src in markers if pattern.search(v)}
    events = sorted((t, v) for v, t in last.items())
    if not events:
        found = sorted({v for _t, v, _s in markers})
        raise SystemExit(f"No markers match {args.events!r}. Marker strings present: {found[:30]}")

    n_pre, n_post = int(args.pre * fs), int(args.post * fs)
    times = np.arange(-n_pre, n_post) / fs
    epochs, names, skipped = [], [], 0
    for t, name in events:
        i0 = int(np.searchsorted(ts, t))
        if i0 - n_pre < 0 or i0 + n_post > len(ts):
            skipped += 1
            continue
        epochs.append(data[i0 - n_pre:i0 + n_post][:, keep].T)
        names.append(name)
    if not epochs:
        raise SystemExit("All matching events were too close to the start/end of the recording.")
    epochs = np.stack(epochs)  # events x channels x samples
    print(f"{len(epochs)} epochs of {args.stream} around {args.events!r}"
          + (f" ({skipped} skipped at recording edges)" if skipped else ""))

    stem = path.with_suffix("")
    out = Path(f"{stem}_{args.stream}_epochs.npz")
    np.savez(out, data=epochs, times=times, labels=np.array([labels[i] for i in keep]),
             units=np.array([units[i] for i in keep]), events=np.array(names), fs=fs)

    fig, axes = plt.subplots(len(keep), 1, figsize=(9, 3 * len(keep)), sharex=True, squeeze=False)
    colours = ["#c62828", "#1565c0", "#2e7d32", "#6a1b9a"]
    groups = args.groups or [None]
    for ax, ci in zip(axes[:, 0], range(len(keep))):
        y = epochs[:, ci, :]
        ax.plot(times, y.T, color="#999", lw=0.6, alpha=0.4)
        for g, colour in zip(groups, colours):
            sel = [i for i, name in enumerate(names) if g is None or g in name]
            if sel:
                ax.plot(times, y[sel].mean(axis=0), color=colour, lw=2,
                        label=f"{g or 'mean'} (n={len(sel)})")
        ax.axvline(0, color="k", lw=0.8, ls="--")
        ax.set_ylabel(f"{labels[keep[ci]]} ({units[keep[ci]]})")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(alpha=0.3)
    axes[-1, 0].set_xlabel(f"seconds from {args.events!r}")
    fig.suptitle(path.name, fontsize=9)
    fig.tight_layout()
    png = Path(f"{stem}_{args.stream}_epochs.png")
    fig.savefig(png, dpi=110)
    print(f"Saved {out}\nSaved {png}")


if __name__ == "__main__":
    main()
