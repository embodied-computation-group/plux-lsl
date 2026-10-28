"""Stream a biosignalsplux hub to Lab Streaming Layer, no OpenSignals needed.

Usage (from the plux-lsl folder):
    .venv\\Scripts\\python.exe plux_lsl.py                  # default hub, 1000 Hz, auto-detect sensors
    .venv\\Scripts\\python.exe plux_lsl.py --ports 1,2,3,4  # force ports (sensors without ID chips)
    .venv\\Scripts\\python.exe plux_lsl.py --list           # print detected sensors and exit

Stops cleanly on Ctrl+C. The stream carries raw ADC counts (int32), with the
first channel being the hub's sample counter (nSeq) so dropped samples show
up as gaps. Unit conversion happens offline using the per-channel metadata.
"""

import argparse
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor" / "plux"))

import plux  # noqa: E402
import lsl_quiet  # noqa: E402,F401
from pylsl import StreamInfo, StreamOutlet, cf_int32  # noqa: E402

DEFAULT_MAC = "00:07:80:4B:3F:25"
DIGITAL_PORT = 9

SENSOR_CLASS = {
    0: "UNKNOWN", 1: "EMG", 2: "ECG", 3: "LIGHT", 4: "EDA", 5: "BVP",
    6: "RESP", 7: "XYZ", 8: "SYNC", 9: "EEG", 10: "SYNC_ADAP", 11: "SYNC_LED",
    12: "SYNC_SW", 13: "USB", 14: "FORCE", 15: "TEMP", 16: "VPROBE",
    17: "BREAKOUT", 18: "OXIMETER", 19: "GONI", 20: "ACT", 21: "EOG",
    22: "EGG", 23: "ANSA", 26: "OSL",
}

# Max sampling rate per number of active analog channels (from PLUX's docs).
MAX_RATE = {1: 8000, 2: 5000, 3: 4000, 4: 3000, 5: 3000, 6: 2000, 7: 2000, 8: 2000}


class Hub(plux.SignalsDev):
    def __init__(self, address):
        plux.MemoryDev.__init__(address)
        self.outlet = None
        self.chunk = []
        self.chunk_size = 10
        self.stop_requested = False
        self.end_seq = None
        self.last_seq = None
        self.n_samples = 0
        self.n_gaps = 0
        self.n_dropped = 0
        self.t_start = None
        self.t_report = None
        self.on_sample = None  # optional hook(nSeq) -> True to stop
        self.quiet = False

    def onRawFrame(self, nSeq, data):
        if self.last_seq is not None and nSeq != self.last_seq + 1:
            self.n_gaps += 1
            self.n_dropped += nSeq - self.last_seq - 1
        self.last_seq = nSeq
        self.n_samples += 1

        self.chunk.append([nSeq, *data])
        if len(self.chunk) >= self.chunk_size:
            self.outlet.push_chunk(self.chunk)
            self.chunk = []

        now = time.perf_counter()
        if self.t_start is None:
            self.t_start = self.t_report = now
        elif now - self.t_report >= 5 and not self.quiet:
            self.t_report = now
            rate = self.n_samples / (now - self.t_start)
            print(f"  {self.n_samples:>9} samples  {rate:7.1f} Hz  "
                  f"gaps {self.n_gaps}  dropped {self.n_dropped}", flush=True)

        if self.on_sample is not None and self.on_sample(nSeq):
            return True
        if self.end_seq is not None and nSeq >= self.end_seq:
            return True
        return self.stop_requested

    def run(self, rate, sources):
        """Acquire until a stop condition, then flush the last partial chunk."""
        self.start(rate, sources)
        self.loop()
        self.stop()
        if self.chunk:
            self.outlet.push_chunk(self.chunk)
            self.chunk = []


def detect_sources(hub, forced_ports, n_bits):
    """Return (sources, channel descriptions) for the ports to record."""
    sensors = hub.getSensors()
    ports = forced_ports or sorted(sensors)
    if not ports:
        sys.exit("No sensors detected. Plug sensors in, or pass --ports 1,2,...")

    sources, channels = [], []
    for port in ports:
        sensor = sensors.get(port)
        kind = SENSOR_CLASS.get(sensor.clas, "UNKNOWN") if sensor else "UNKNOWN"
        serial = sensor.serialNum if sensor else ""

        src = plux.Source()
        src.port = port
        src.freqDivisor = 1
        src.nBits = n_bits
        if port == DIGITAL_PORT:
            # SpO2/fNIRS on the digital port deliver two derivations (red, infrared).
            src.chMask = 0x03
            channels += [(f"{kind}_RED{port}", kind, port, serial),
                         (f"{kind}_IR{port}", kind, port, serial)]
        else:
            src.chMask = 0x01
            channels.append((f"{kind}{port}", kind, port, serial))
        sources.append(src)
    return sources, channels


def make_outlet(mac, rate, n_bits, channels, properties):
    source_id = "plux-" + mac.replace(":", "")
    info = StreamInfo("PLUX", "Biosignals", 1 + len(channels), rate, cf_int32, source_id)
    desc = info.desc()
    desc.append_child_value("manufacturer", "PLUX")
    acq = desc.append_child("acquisition")
    acq.append_child_value("device", "biosignalsplux")
    acq.append_child_value("mac", mac)
    acq.append_child_value("resolution_bits", str(n_bits))
    for key, value in properties.items():
        acq.append_child_value(str(key), str(value))
    chans = desc.append_child("channels")
    ch = chans.append_child("channel")
    ch.append_child_value("label", "nSeq")
    ch.append_child_value("type", "counter")
    ch.append_child_value("unit", "samples")
    for label, kind, port, serial in channels:
        ch = chans.append_child("channel")
        ch.append_child_value("label", label)
        ch.append_child_value("type", kind)
        ch.append_child_value("unit", "raw")
        ch.append_child_value("port", str(port))
        ch.append_child_value("sensor_serial", str(serial))
    return StreamOutlet(info, chunk_size=0, max_buffered=360)


def add_device_args(ap):
    ap.add_argument("--mac", default=DEFAULT_MAC, help=f"hub Bluetooth MAC (default {DEFAULT_MAC})")
    ap.add_argument("--rate", type=int, default=1000, help="sampling rate in Hz (default 1000)")
    ap.add_argument("--bits", type=int, choices=(8, 16), default=16, help="ADC resolution (default 16)")
    ap.add_argument("--ports", help="comma-separated ports to record instead of auto-detect, e.g. 1,2,3,4,9")


def connect(args):
    """Connect to the hub and report it. Returns (hub, sources, channels, properties)."""
    forced = [int(p) for p in args.ports.split(",")] if args.ports else None

    print(f"Connecting to biosignalsplux {args.mac} ...", flush=True)
    hub = Hub("BTH" + args.mac)
    try:
        print(f"Battery: {int(hub.getBattery())}%")
        try:
            properties = hub.getProperties()
        except Exception:
            properties = {}
        if properties:
            print("Device:", ", ".join(f"{k}={v}" for k, v in properties.items()))

        sources, channels = detect_sources(hub, forced, args.bits)
        print("Channels:")
        for label, kind, port, serial in channels:
            print(f"  port {port}: {label:<14} {kind:<9} serial {serial}")

        n_analog = sum(1 for s in sources if s.port != DIGITAL_PORT)
        limit = MAX_RATE.get(max(n_analog, 1), 2000)
        if args.rate > limit:
            sys.exit(f"--rate {args.rate} exceeds the {limit} Hz limit for {n_analog} analog channels")
    except BaseException:
        hub.close()
        raise
    return hub, sources, channels, properties


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_device_args(ap)
    ap.add_argument("--duration", type=float, help="stop after this many seconds (default: until Ctrl+C)")
    ap.add_argument("--stop-on-eof", action="store_true", help="also stop when stdin closes (used by physio_app.py)")
    ap.add_argument("--list", action="store_true", help="print detected sensors and battery, then exit")
    args = ap.parse_args()

    hub, sources, channels, properties = connect(args)
    try:
        if args.list:
            return
        hub.outlet = make_outlet(args.mac, args.rate, args.bits, channels, properties)
        if args.duration:
            hub.end_seq = int(args.duration * args.rate) - 1
        signal.signal(signal.SIGINT, lambda *_: setattr(hub, "stop_requested", True))
        if args.stop_on_eof:
            def wait_for_eof():
                sys.stdin.read()
                hub.stop_requested = True
            threading.Thread(target=wait_for_eof, daemon=True).start()

        print(f"Streaming LSL 'PLUX' at {args.rate} Hz. Ctrl+C to stop.", flush=True)
        hub.run(args.rate, sources)
        print(f"Stopped: {hub.n_samples} samples, {hub.n_gaps} gaps, {hub.n_dropped} dropped.")
    finally:
        hub.close()


if __name__ == "__main__":
    main()
