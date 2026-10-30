"""RRST-Kids markers (stream "RRST"): recognise the kids task and parse its trials and slurps.

The marker list is in rrst-toolkit\\task\\RRST_kids\\README.md ("Markers"). The adult
task (RRST_2-0) sends trial_<n>_interval<k>_... markers instead; check_xdf.py and
rrst_figures.py handle it themselves.
"""

import re

LEVEL = re.compile(r"trial_(\d+)_level_(.+)")
DEMO = re.compile(r"demo_([a-z]+)_level_(.+)")
GO = re.compile(r"trial_(\d+)_go")
SLURP_ONSET = re.compile(r"slurp_(\d+)_onset")
SLURP_END = re.compile(r"slurp_(\d+)_end_([-\d.]+)_([-\d.]+)")
SLURP_NOT = re.compile(r"slurp_(\d+)_(weak|overlong)")
OUTCOME = re.compile(r"trial_(\d+)_(correct|wrong)")
ANSWERS = {"answer_yes": "yes", "answer_no": "no", "bad_breath": "bad", "no_answer": "none", "pause": "pause"}
# Markers only the kids task sends, and only the adult task sends
KIDS_ONLY = re.compile(r"story|calibration_(start|end)|freeplay_(start|end)|demo_.+|practice_start|main_start"
                       r"|trial_\d+_go|slurp_\d+_.+|question|answer_(yes|no)|bad_breath|no_answer"
                       r"|rest_(start|end)|aborted|done")
ADULT_ONLY = re.compile(r"session_start|session_end_.+|trial_\d+_interval\d_.+|trial_\d+_(no)?response"
                        r"|trial_\d+_confidence|trial_\d+_iti|break_(start|end)")


def is_kids(values):
    """True for an RRST-Kids recording: kids-only markers and no adult-only ones."""
    if any(ADULT_ONLY.fullmatch(v) for v in values):
        return False
    return any(KIDS_ONLY.fullmatch(v) or LEVEL.fullmatch(v) for v in values)


def _num(text):
    try:
        return float(text)
    except ValueError:
        return None


def parse_kids(values, times=None):
    """Trials and slurps of a kids session, in order.

    Returns {"trials": [...], "end": "done" | "aborted" | None, "main_started": bool}. Each trial:
    {"n", "phase": freeplay | demo | practice | main, "demo", "level", "t", "go", "question",
     "answer": yes | no | bad | none | pause | None, "correct": True | False | None,
     "free": free play or a free trial (no question), "slurps": [...]};
    each slurp {"k", "onset", "end", "status": counted | weak | overlong | None, "volume", "peak"}.
    Times are the marker times (the marker's index without `times`). Free play is one "trial"
    with n None.
    """
    times = list(times) if times is not None else list(range(len(values)))
    trials, end, phase, cur = [], None, "practice", None

    def new(**kw):
        tr = dict(n=None, phase=phase, demo=None, level=None, t=None, go=None, question=None,
                  answer=None, correct=None, slurps=[])
        tr.update(kw)
        trials.append(tr)
        return tr

    for v, t in zip(values, times):
        if v == "practice_start":
            phase = "practice"
        elif v == "main_start":
            phase = "main"
        elif v == "freeplay_start":
            cur = new(phase="freeplay", level=0.0, t=t)
        elif v == "freeplay_end":
            cur = None
        elif v in ("done", "aborted"):
            end = v
        elif v == "question" and cur is not None:
            cur["question"] = t
        elif v in ANSWERS and cur is not None:
            cur["answer"] = ANSWERS[v]
        elif m := LEVEL.fullmatch(v):
            cur = new(n=int(m[1]), level=_num(m[2]), t=t)
        elif m := DEMO.fullmatch(v):
            cur = new(phase="demo", demo=m[1], level=_num(m[2]), t=t)
        elif m := GO.fullmatch(v):
            n = int(m[1])
            if cur is None or cur["phase"] == "freeplay" or cur["n"] not in (None, n):
                cur = new(n=n, t=t)
            cur["n"], cur["go"] = n, t
        elif m := SLURP_ONSET.fullmatch(v):
            if cur is None:
                cur = new(phase="freeplay", level=0.0, t=t)
            cur["slurps"].append(dict(k=int(m[1]), onset=t, end=None, status=None, volume=None, peak=None))
        elif m := SLURP_END.fullmatch(v) or SLURP_NOT.fullmatch(v):
            k = int(m[1])
            if cur is None:
                cur = new(phase="freeplay", level=0.0, t=t)
            s = next((s for s in reversed(cur["slurps"]) if s["k"] == k and s["status"] is None), None)
            if s is None:  # end without its onset (recording started mid-slurp)
                s = dict(k=k, onset=None, end=None, status=None, volume=None, peak=None)
                cur["slurps"].append(s)
            s["end"] = t
            if m.re is SLURP_END:
                s.update(status="counted", volume=_num(m[2]), peak=_num(m[3]))
            else:
                s["status"] = m[2]
        elif m := OUTCOME.fullmatch(v):
            n = int(m[1])
            tr = next((tr for tr in reversed(trials) if tr["n"] == n), None)
            if tr is not None:
                tr["correct"] = m[2] == "correct"
    for i, tr in enumerate(trials):  # free trials (earlier versions, tutorial off) have no question;
        # the last trial without one may just be unfinished
        tr["free"] = tr["phase"] == "freeplay" or (
            tr["phase"] == "practice" and tr["go"] is not None and tr["question"] is None
            and tr["answer"] is None and i < len(trials) - 1)
    return {"trials": trials, "end": end, "main_started": phase == "main"}


def kids_summary(values, session_end=None):
    """Summary lines [(text, ok)] for a kids recording; ok None = for information.

    A trial counts once the experimenter's entry follows its level marker (answer_yes / _no,
    bad_breath, no_answer, pause). An aborted session is a normal outcome. session_end: the
    recorder's <task>_end / _end_early marker, if the recording was stopped by the app.
    """
    p = parse_kids(values)
    trials = p["trials"]
    done = {ph: [t for t in trials if t["phase"] == ph and t["answer"] is not None] for ph in ("practice", "main")}
    n_done = len(done["practice"]) + len(done["main"])
    if done["main"]:
        where = f"{len(done['main'])} main trials"
    elif p["main_started"]:
        where = "in the main part, before its first trial"
    elif done["practice"]:
        where = "practice only, no main trials"
    else:
        where = "before the first trial"
    if p["end"] == "done":
        first = (f"RRST-Kids: finished ({where})", True)
    elif p["end"] == "aborted":
        first = (f"RRST-Kids: aborted by the experimenter after {n_done} trials ({where})", True)
    elif session_end:
        first = (f"RRST-Kids: recording stopped while the task was still running (no done/aborted marker), "
                 f"after {n_done} trials ({where})", None)
    else:
        first = (f"RRST-Kids: after {n_done} trials, no done/aborted marker - task not finished "
                 "when recording stopped?", False)

    def part(ph):
        c = sum(t["correct"] is True for t in done[ph])
        w = sum(t["correct"] is False for t in done[ph])
        return f"{ph} {len(done[ph])} ({c} correct, {w} wrong)" if done[ph] else f"{ph} 0"

    free = sum(t["free"] and t["phase"] == "practice" for t in trials)
    slurps = [s for t in trials for s in t["slurps"]]
    count = {st: sum(s["status"] == st for s in slurps) for st in ("counted", "weak", "overlong")}
    text = f"RRST-Kids trials: {part('practice')}, {part('main')}"
    if free:
        text += f", {free} free trials"
    text += f"; slurps {len(slurps)} ({count['counted']} counted"
    text += "".join(f", {count[k]} {label}" for k, label in (("weak", "weak"), ("overlong", "too long"))
                    if count[k])
    text += ")"
    if not slurps and any(t["go"] is not None for t in trials):
        text += ", no slurp markers (airflow sensor off in the task?)"
    missing = [t["n"] for ph in done for t in done[ph] if t["answer"] in ("yes", "no") and t["correct"] is None]
    ok = True
    if missing:
        text += f" - triggers incomplete: no trial_<n>_correct/wrong for trial {', '.join(map(str, missing))}"
        ok = False
    return [first, (text, ok)]
