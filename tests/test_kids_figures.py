import numpy as np

from test_rrst_markers import KIDS


def test_kids_figures_from_synthetic_session(tmp_path):
    import rrst_figures
    times = np.arange(len(KIDS)) * 1.5 + 10
    fs = 100
    t = np.arange(0, times[-1] + 20, 1 / fs)
    x = np.zeros_like(t)
    for ts, v in zip(times, KIDS):
        if v.endswith("_onset"):
            x[(t >= ts) & (t < ts + 1)] = 60.0
    rrst_figures.kids_figures((t, x, fs), None, list(zip(times, KIDS)), tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {"1_session.png", "2_trials.png", "3_slurps.png"}
