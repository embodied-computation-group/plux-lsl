"""Signal-quality checks and unit conversion shared by viewer.py and check_xdf.py."""

import numpy as np
from scipy import signal as sps

MAINS_HZ = 50    # Switzerland / Denmark

OK, WARN, BAD = "#1a7f37", "#b26a00", "#c62828"


def to_units(kind, raw, bits):
    """PLUX transfer functions (biosignalsplux sensor datasheets, VCC = 3 V)."""
    x = raw / 2 ** bits
    if kind == "ECG":
        return (x - 0.5) * 3.0 / 1019 * 1000, "mV"
    if kind == "RESP":
        return (x - 0.5) * 100, "%"
    if kind == "EDA":
        return x * 3.0 / 0.12, "µS"
    return raw, "raw"


def check_common(raw, bits):
    top = 2 ** bits - 1
    if raw.std() < 2:
        return BAD, "FLAT - sensor not connected or not on the body"
    clipped = np.mean((raw <= 0) | (raw >= top))
    if clipped > 0.01:
        return BAD, f"CLIPPING {clipped:.0%} - check placement / reduce movement"
    return None


def check_ecg(x, fs):
    sos = sps.butter(3, [5, 30], "bandpass", fs=fs, output="sos")
    f = sps.sosfiltfilt(sos, x)
    freqs, psd = sps.welch(x - x.mean(), fs=fs, nperseg=min(len(x), 2 * fs))
    mains = psd[(freqs > MAINS_HZ - 2) & (freqs < MAINS_HZ + 2)].sum()
    ecg_band = psd[(freqs > 1) & (freqs < 40)].sum()
    if mains > ecg_band:
        # Mains noise can fake regular "beats", so never report a heart rate here.
        return BAD, f"{MAINS_HZ} Hz mains noise - check electrodes are on skin and leads clipped"
    env = np.abs(f)
    top = np.percentile(env, 99)
    if top < 5 * np.median(env):  # R peaks must stand well clear of the baseline
        return WARN, "no clear heartbeat - check electrodes and lead colours"
    peaks, _ = sps.find_peaks(env, distance=int(0.33 * fs), height=top * 0.4)
    if len(peaks) >= 4:
        rr = np.diff(peaks) / fs
        hr = 60 / np.median(rr)
        if 35 <= hr <= 180 and rr.std() / rr.mean() < 0.2:
            return OK, f"HR {hr:.0f} bpm"
    return WARN, "no clear heartbeat - check electrodes and lead colours"


def check_resp(x, fs):
    sos = sps.butter(2, 1.0, "lowpass", fs=fs, output="sos")
    f = sps.sosfiltfilt(sos, x)
    f = sps.detrend(f)
    span = np.percentile(f, 95) - np.percentile(f, 5)
    peaks, _ = sps.find_peaks(f, distance=int(1.5 * fs), prominence=0.3 * span)
    secs = len(x) / fs
    if len(peaks) >= 3 and secs >= 15:
        intervals = np.diff(peaks) / fs
        bpm = 60 / intervals.mean()
        if 4 <= bpm <= 40 and intervals.std() / intervals.mean() < 0.5:  # roughly regular
            return OK, f"{bpm:.0f} breaths/min"
    if secs < 15:
        return WARN, "collecting..."
    return WARN, "no clear breathing - check belt tension / position"


def display_filter(kind, x, fs):
    if kind == "ECG":
        sos = sps.butter(2, [0.5, 40], "bandpass", fs=fs, output="sos")
        return sps.sosfiltfilt(sos, x)
    if kind == "RESP":
        sos = sps.butter(2, 2.0, "lowpass", fs=fs, output="sos")
        return sps.sosfiltfilt(sos, x)
    return x
