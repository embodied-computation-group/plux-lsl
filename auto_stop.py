"""When a task recording stops by itself: after the task's end marker, or when its trigger stream is gone.

Pure logic (no Tk, no LSL), so it can be tested. physio_app.py feeds it the end marker (seen by
the marker counter) and the result of every resolve of the task's trigger stream, and asks
reason() on every tick.
"""

END_GRACE_S = 5    # after the task's end marker, keep recording this long (trailing physiology)
GONE_S = 15        # the recorded run's trigger stream unresolvable this long = task crashed or closed
GONE_MISSES = 3    # ... and missed this many resolves in a row: a single missed resolve never stops
GONE_TEXT = "closed before it finished. Recording saved."  # banner: "...: <task> closed before ..."


def is_end_marker(label, prefix):
    """The marker's first whitespace-separated token starts with the task's end prefix.

    "HRD_end" matches "HRD_end" and "HRD_end_early too_slow"; "session_end" matches
    "session_end_complete"; "task_end" matches "task_end_early". "trial_end" never matches."""
    if not prefix or not isinstance(label, str):
        return False
    tokens = label.split(maxsplit=1)
    return bool(tokens) and tokens[0].startswith(prefix)


class AutoStop:
    """One task recording. Times are time.monotonic() seconds."""

    def __init__(self, grace_s=END_GRACE_S, gone_s=GONE_S, gone_misses=GONE_MISSES):
        self.grace_s, self.gone_s, self.gone_misses = grace_s, gone_s, gone_misses
        self.end_label = None   # the first end marker, and when it arrived
        self.end_t = None
        self.seen = False       # the recorded run's stream was resolved during this recording
        self.gone_since = None  # first miss of the current run of misses
        self.misses = 0
        self.gone = False

    def ended(self, label, t):
        """The task sent its end marker (only the first one counts)."""
        if self.end_t is None:
            self.end_label, self.end_t = label, t

    def resolved(self, present, t):
        """Result of one resolve: is the recorded run's trigger stream online?"""
        if present:
            self.seen, self.gone_since, self.misses, self.gone = True, None, 0, False
            return
        if not self.seen:  # never online during this recording: nothing to lose
            return
        if self.gone_since is None:
            self.gone_since = t
        self.misses += 1
        self.gone = self.misses >= self.gone_misses and t - self.gone_since >= self.gone_s

    def reason(self, now):
        """None while recording should go on, else why it stops: "ended (<label>)" once the grace
        after the end marker has passed, or GONE_TEXT when the stream is gone."""
        if self.end_t is not None:  # end marker seen: always wait out the grace
            return f"ended ({self.end_label})" if now - self.end_t >= self.grace_s else None
        if self.gone:
            return GONE_TEXT
        return None
