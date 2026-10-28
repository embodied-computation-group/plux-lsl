"""Signal-quality check for the airflow stream (flow_lsl.py), same style as quality.py.

The sensor sits at the inlet of the RRST circuit with its arrow toward the
participant, so inhalations are positive and exhalations leave through the
T-valve (flow near zero between breaths).
"""

import numpy as np
from scipy import signal as sps

from quality import OK, WARN, BAD

RANGE_SLM = (-10.0, 240.0)   # SFM3019-240 measurement range
MIN_BREATH_SLM = 3.0         # a deliberate RRST inhalation peaks well above this


def check_flow(x, fs):
    """Return (colour, message) for a window of flow in slm."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < fs:
        return WARN, "collecting..."
    if x.std() == 0:
        return BAD, "FROZEN - flow sensor not updating; restart the flow stream"
    out = np.mean((x <= RANGE_SLM[0] + 0.1) | (x >= RANGE_SLM[1] - 1))
    if out > 0.01:
        return BAD, "OUT OF RANGE - flow beyond the sensor's limits"
    # A small backflow (about -2 slm for 1-2 s) follows every breath as the T-valve
    # closes (bench 2026-10-28). A reversed sensor shows whole breaths as large negative flow.
    if np.mean(x < -5) > 0.01:
        return WARN, "REVERSE FLOW - sensor arrow should point toward the participant"

    sos = sps.butter(2, 5.0, "lowpass", fs=fs, output="sos")
    f = sps.sosfiltfilt(sos, x)
    if np.median(f) > MIN_BREATH_SLM:
        return WARN, "CONSTANT FLOW - air is moving all the time; check for a leak or draught"
    peaks, _ = sps.find_peaks(f, height=MIN_BREATH_SLM, distance=int(1.0 * fs))
    if len(peaks) == 0:
        return WARN, "no breaths through the circuit - mouthpiece in? circuit sealed?"
    return OK, f"{len(peaks)} breaths, peak {np.median(f[peaks]):.0f} slm"
