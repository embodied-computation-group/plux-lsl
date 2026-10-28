"""Send a numbered LSL marker every second, standing in for a task's event markers.

Usage: .venv\\Scripts\\python.exe send_test_markers.py [n_markers]
"""

import sys
import time

import lsl_quiet  # noqa: F401  (must precede pylsl)
from pylsl import StreamInfo, StreamOutlet, cf_string, local_clock

n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
outlet = StreamOutlet(StreamInfo("TestMarkers", "Markers", 1, 0, cf_string, "plux-lsl-test-markers"))
print("Waiting 3 s for LabRecorder to pick up the marker stream ...", flush=True)
time.sleep(3)
for i in range(1, n + 1):
    outlet.push_sample([f"marker_{i}"], local_clock())
    print(f"marker_{i}", flush=True)
    time.sleep(1)
