"""Live view of the PLUX LSL stream with plain-language signal checks.

Usage: .venv\\Scripts\\python.exe viewer.py
(record.py opens this automatically.) Each channel's title turns green when
the signal looks usable, amber or red with a hint when it does not.
"""

import argparse
import sys
import time

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

from monitor import BAD, OK, WARN, Monitor, draw, find_plux


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", help="save a PNG after --after seconds and exit (for testing)")
    ap.add_argument("--after", type=float, default=15)
    args = ap.parse_args()

    print("Looking for the PLUX stream ...", flush=True)
    info = find_plux(timeout=15)
    if info is None:
        sys.exit("No PLUX stream found. Is the hub on and plux_lsl.py / record.py running?")
    mon = Monitor(info)

    fig, axes = plt.subplots(len(mon.channels), 1, figsize=(11, 2.6 * len(mon.channels) + 0.6),
                             sharex=True, squeeze=False)
    axes = axes[:, 0]
    fig.canvas.manager.set_window_title("PLUX signal check")
    for ax in axes:
        ax.grid(alpha=0.3)
    axes[-1].set_xlabel("seconds")
    banner = fig.text(0.01, 0.995, "", va="top", fontsize=11, weight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.96))

    def update(_):
        mon.poll()
        if mon.stalled:
            banner.set_text("NO DATA - hub off, out of range or battery empty?")
            banner.set_color(BAD)
        else:
            banner.set_text(f"Streaming {mon.fs} Hz  |  dropped samples: {mon.dropped}")
            banner.set_color(OK if mon.dropped == 0 else WARN)
        draw(axes, mon, mon.status())

    if args.snapshot:
        t_end = time.monotonic() + args.after
        while time.monotonic() < t_end:
            update(None)
            plt.pause(0.2)
        fig.savefig(args.snapshot, dpi=90)
        print(f"Saved {args.snapshot}")
        return
    anim = FuncAnimation(fig, update, interval=200, cache_frame_data=False)  # noqa: F841
    plt.show()


if __name__ == "__main__":
    main()
