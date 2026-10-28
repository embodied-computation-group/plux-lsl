"""Live LSL stream buffer with per-channel quality status (used by viewer.py and physio_app.py).

Works for any stream whose first channel is an "nSeq" sample counter: PLUX
(raw ADC counts, converted with quality.to_units) and Flow (already in slm).
"""

import time

import lsl_quiet  # noqa: F401  (must precede pylsl)
import numpy as np
from pylsl import StreamInlet, resolve_byprop

from flow_quality import check_flow
from quality import BAD, OK, WARN, check_common, check_ecg, check_resp, display_filter, to_units

SHOW_S = 10      # seconds on screen
KEEP_S = 30      # seconds kept for rate estimates
DISPLAY_HZ = 200
STALL_S = 2


def find_stream(name, timeout):
    found = resolve_byprop("name", name, timeout=timeout)
    return found[0] if found else None


def find_plux(timeout):
    return find_stream("PLUX", timeout)


class Monitor:
    def __init__(self, stream_info, kinds=None):
        """kinds: channel types to show/check (default all), e.g. {"FLOW"} to skip temperature."""
        self.inlet = StreamInlet(stream_info, max_buflen=KEEP_S, recover=True)
        info = self.inlet.info()
        self.name = info.name()
        self.fs = int(round(info.nominal_srate()))
        bits = info.desc().child("acquisition").child_value("resolution_bits")
        self.bits = int(bits) if bits else None  # PLUX only: raw ADC counts
        cols, ch = [], info.desc().child("channels").child("channel")
        while not ch.empty():
            cols.append((ch.child_value("label"), ch.child_value("type"), ch.child_value("unit")))
            ch = ch.next_sibling()
        # (column index, label, kind, unit) for the channels shown; column 0 is nSeq
        self.cols = [(i, lab, kind, unit) for i, (lab, kind, unit) in enumerate(cols)
                     if i > 0 and (kinds is None or kind in kinds)]
        self.channels = [(lab, kind) for _i, lab, kind, _u in self.cols]
        self.buf = np.zeros((0, len(cols)))
        self.dropped = 0
        self.last_seq = None
        self.last_data = time.monotonic()

    def poll(self):
        data, _ = self.inlet.pull_chunk(timeout=0.0, max_samples=self.fs * KEEP_S)
        if not data:
            return
        d = np.asarray(data, dtype=float)
        seq = d[:, 0].astype(np.int64)
        if self.last_seq is not None:
            seq = np.concatenate([[self.last_seq], seq])
        steps = np.diff(seq)
        self.dropped += int((steps[steps > 1] - 1).sum())
        self.last_seq = int(d[-1, 0])
        self.last_data = time.monotonic()
        self.buf = np.vstack([self.buf, d])[-KEEP_S * self.fs:]

    @property
    def stalled(self):
        return time.monotonic() - self.last_data > STALL_S

    @property
    def seconds_buffered(self):
        return len(self.buf) / self.fs

    def _values(self, col, kind, unit, n=None):
        raw = self.buf[-n:, col] if n else self.buf[:, col]
        if self.bits is None:
            return raw, raw, unit
        x, unit = to_units(kind, raw, self.bits)
        return raw, x, unit

    def status(self):
        """[(label, kind, colour, message)] for each shown channel, judged on recent data."""
        if len(self.buf) < self.fs:
            return [(label, kind, WARN, "collecting...") for label, kind in self.channels]
        n, out = SHOW_S * self.fs, []
        for col, label, kind, unit in self.cols:
            raw, x, _ = self._values(col, kind, unit)
            res = check_common(raw[-n:], self.bits) if self.bits else None
            if res is None:
                if kind == "ECG":
                    res = check_ecg(x[-n:], self.fs)
                elif kind == "RESP":
                    res = check_resp(x, self.fs)
                elif kind == "FLOW":
                    res = check_flow(x, self.fs)
                else:
                    res = (OK, "")
            out.append((label, kind, *res))
        return out

    def traces(self):
        """[(t, y, unit)] display-ready traces of the last SHOW_S seconds."""
        if len(self.buf) < self.fs:
            return []
        n, step = SHOW_S * self.fs, max(self.fs // DISPLAY_HZ, 1)
        out = []
        for col, _label, kind, unit in self.cols:
            _raw, x, unit = self._values(col, kind, unit, n)
            y = display_filter(kind, x, self.fs)[::step]
            t = (np.arange(len(y)) - len(y)) * step / self.fs
            out.append((t, y, unit))
        return out


def draw(axes, monitor, statuses):
    """Update matplotlib axes (one per shown channel) from a Monitor."""
    for ax, (t, y, unit), (label, _kind, colour, msg) in zip(axes, monitor.traces(), statuses):
        line = ax.lines[0] if ax.lines else ax.plot([], [], lw=0.8, color="#333")[0]
        line.set_data(t, y)
        lo, hi = np.percentile(y, [0.5, 99.5])
        pad = (hi - lo) * 0.15 or 1
        ax.set_xlim(-SHOW_S, 0)
        ax.set_ylim(lo - pad, hi + pad)
        ax.set_ylabel(unit)
        ax.set_title(f"{label}   {msg}", loc="left", color=colour, fontsize=11, weight="bold")


__all__ = ["BAD", "OK", "WARN", "Monitor", "draw", "find_plux", "find_stream"]
