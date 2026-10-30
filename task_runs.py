"""Which runs of a task's marker stream have been recorded: decides when to auto-start.

Every run of the task opens a new LSL stream (a new uid). The app resolves the stream
in every state, so a run that appears while a recording is starting, running or being
saved is kept here and auto-started once the app is back to checking. A run is
auto-started once only; a run that was recorded and is still running after Stop is
not restarted (the RA clicks Start recording to record it again).
"""


class TaskRuns:
    def __init__(self):
        self.current = None   # uid of the newest stream the last resolve found; None = not online
        self.recorded = set()

    def seen(self, uid):
        """Result of a resolve (uid or None). Returns True if it changed."""
        changed = uid != self.current
        self.current = uid
        return changed

    def mark_recorded(self, *uids):
        """A recording started with these streams online."""
        self.recorded.update(u for u in uids if u)

    def is_new(self):
        """The task is running and this run has not been recorded."""
        return self.current is not None and self.current not in self.recorded

    def current_recorded(self):
        return self.current is not None and self.current in self.recorded

    def should_auto_start(self, state, devices_ready):
        return state == "checking" and devices_ready and self.is_new()
