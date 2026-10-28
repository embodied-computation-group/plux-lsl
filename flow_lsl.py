"""Stream a Sensirion SFM3019 flow sensor (via SEK-SensorBridge USB) to Lab Streaming Layer.

Usage (from the plux-lsl folder):
    .venv\\Scripts\\python.exe flow_lsl.py               # auto-find the bridge, 1000 Hz
    .venv\\Scripts\\python.exe flow_lsl.py --list        # print bridge, sensor and a 1 s reading, then exit
    .venv\\Scripts\\python.exe flow_lsl.py --port COM10  # skip auto-detection

The SensorBridge samples the sensor on its own clock and buffers the readings,
so samples are evenly spaced even when Windows is busy. Stops cleanly on Ctrl+C.
Channels: nSeq (bridge sample counter, gaps = lost samples), flow (slm, standard
litres per minute at 20 degC), temperature (degC). Needs the SensorBridge driver,
which comes with Sensirion's SEK-ControlCenter installer. ControlCenter must be
closed while this runs, because only one program can hold the port.
"""

import argparse
import signal
import sys
import threading
import time

import lsl_quiet  # noqa: F401
from pylsl import StreamInfo, StreamOutlet, cf_double64, local_clock
from serial import SerialException
from serial.tools import list_ports
from sensirion_shdlc_driver import ShdlcConnection, ShdlcSerialPort
from sensirion_shdlc_driver.errors import ShdlcDeviceError
from sensirion_shdlc_sensorbridge import SensorBridgePort, SensorBridgeShdlcDevice
from sensirion_sensorbridge_i2c_sfm.sfm3019.commands import Sfm3019I2cCmdReadMeas
from sensirion_sensorbridge_i2c_sfm.sfm3019.device import Sfm3019I2cSensorBridgeDevice
from sensirion_sensorbridge_i2c_sfm.sfm3019.sfm3019_constants import MeasurementMode

BRIDGE_VID, BRIDGE_PID = 0x0403, 0x7168   # SEK-SensorBridge (FTDI chip, Sensirion product ID)
BAUDRATE = 460800
SFM_ADDRESS = 0x2E
SUPPLY_V = 3.3
I2C_HZ = 400e3
PACKET = 6 + 1   # flow + temp words with CRC, plus the bridge's status byte


def find_port():
    ports = [p.device for p in list_ports.comports() if (p.vid, p.pid) == (BRIDGE_VID, BRIDGE_PID)]
    if not ports:
        sys.exit("SensorBridge not found.\n"
                 "  - Is its USB cable plugged in (the bridge LED should be on)?\n"
                 "  - Is the SensorBridge driver installed? It comes with SEK-ControlCenter.")
    if len(ports) > 1:
        sys.exit(f"More than one SensorBridge found ({', '.join(ports)}). Pick one with --port.")
    return ports[0]


class Bridge:
    def __init__(self, port):
        try:
            self.serial = ShdlcSerialPort(port=port, baudrate=BAUDRATE)
        except SerialException as e:
            if "denied" in str(e).lower() or "busy" in str(e).lower():
                sys.exit(f"{port} is in use by another program. Close SEK-ControlCenter "
                         "(or any other flow recording) and try again.")
            raise
        self.dev = SensorBridgeShdlcDevice(ShdlcConnection(self.serial), slave_address=0)
        self.port = port
        self.bridge_port = None
        self.sfm = None
        self.handle = None
        self.stop_requested = False
        self.n_samples = 0
        self.n_gaps = 0
        self.n_dropped = 0

    def close(self):
        try:
            self.dev.stop_repeated_i2c_transceive()
            if self.sfm is not None:
                self.sfm.stop_continuous_measurement()
            for p in (SensorBridgePort.ONE, SensorBridgePort.TWO):
                self.dev.switch_supply_off(p)
        except Exception:
            pass   # bridge unplugged or already closed
        self.serial.close()

    def connect(self):
        """Power both bridge ports, find the SFM3019 and start it measuring air."""
        self.dev.stop_repeated_i2c_transceive()   # left running if a previous run crashed
        for p in (SensorBridgePort.ONE, SensorBridgePort.TWO):
            self.dev.set_i2c_frequency(p, frequency=I2C_HZ)
            self.dev.set_supply_voltage(p, voltage=SUPPLY_V)
            self.dev.switch_supply_on(p)
        time.sleep(0.1)
        for p in (SensorBridgePort.ONE, SensorBridgePort.TWO):
            if SFM_ADDRESS in self.dev.scan_i2c(p):
                self.bridge_port = p
            else:
                self.dev.switch_supply_off(p)
        if self.bridge_port is None:
            sys.exit("SensorBridge found, but no flow sensor on either of its ports.\n"
                     "  - Check the sensor cable is pushed fully into the bridge.")
        self.sfm = Sfm3019I2cSensorBridgeDevice(self.dev, self.bridge_port, SFM_ADDRESS)
        product, serial = self.sfm.read_product_identifier_and_serial_number()
        self.sfm.initialize_sensor(MeasurementMode.Air)
        self.sfm.start_continuous_measurement(MeasurementMode.Air)
        time.sleep(0.05)
        return {
            "bridge_serial": self.dev.get_serial_number(),
            "bridge_version": str(self.dev.get_version()),
            "bridge_port": 1 if self.bridge_port == SensorBridgePort.ONE else 2,
            "sensor_product_id": f"{product:08X}",
            "sensor_serial": str(serial),
            "flow_unit": self.sfm.flow_unit,
            "gas": "air",
        }

    def read_once(self, seconds):
        """Software-polled reading for --list. Returns (mean, sd, temp)."""
        flows, temp = [], None
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < seconds:
            flow, temp = self.sfm.read_continuous_measurement()
            flows.append(flow)
        mean = sum(flows) / len(flows)
        sd = (sum((f - mean) ** 2 for f in flows) / len(flows)) ** 0.5
        return mean, sd, temp

    def run(self, outlet, rate, end_samples=None):
        """Acquire on the bridge clock and push chunks until a stop condition."""
        cmd = Sfm3019I2cCmdReadMeas()

        def start():
            self.dev.stop_repeated_i2c_transceive()
            return self.dev.start_repeated_i2c_transceive(
                self.bridge_port, int(1e6 / rate), SFM_ADDRESS, b"", PACKET - 1, 1000)

        self.handle = start()
        restarts = 0
        seq = 0
        t_start = t_report = time.perf_counter()
        while not self.stop_requested:
            # The bridge answers with every sample taken up to this request, so the
            # request time stamps the newest sample. One read takes ~20 ms (FTDI latency).
            t_req = local_clock()
            try:
                buf = self.dev.read_buffer(self.handle)
            except ShdlcDeviceError as e:
                # Seen once right after the bridge was re-plugged: the first read was
                # refused ("parameter out of range"). Restarting the sampling fixed it.
                if self.n_samples or restarts >= 2:
                    raise
                restarts += 1
                print(f"Bridge refused the first read ({e}); restarting the sampling.", flush=True)
                time.sleep(0.2)
                self.handle = start()
                continue
            lost = buf.lost_bytes // PACKET
            if lost:
                self.n_gaps += 1
                self.n_dropped += lost
                seq += lost
            chunk = []
            for v in buf.values:
                try:
                    if v.error:
                        raise v.error
                    flow, temp = self.sfm._convert_measurement_data(cmd.interpret_response(v.data))
                    chunk.append([seq, flow, temp])
                except Exception:
                    self.n_gaps += 1   # I2C or CRC error: leave a gap in nSeq
                    self.n_dropped += 1
                seq += 1
            if chunk:
                outlet.push_chunk(chunk, t_req)
                self.n_samples += len(chunk)

            now = time.perf_counter()
            if now - t_report >= 5:
                t_report = now
                print(f"  {self.n_samples:>9} samples  {self.n_samples / (now - t_start):7.1f} Hz  "
                      f"gaps {self.n_gaps}  dropped {self.n_dropped}  flow {chunk[-1][1] if chunk else float('nan'):6.2f} slm",
                      flush=True)
            if end_samples is not None and seq >= end_samples:
                break


def make_outlet(rate, info_props):
    info = StreamInfo("Flow", "Airflow", 3, rate, cf_double64, "flow-" + info_props["sensor_serial"])
    desc = info.desc()
    desc.append_child_value("manufacturer", "Sensirion")
    acq = desc.append_child("acquisition")
    acq.append_child_value("device", "SFM3019 via SEK-SensorBridge")
    acq.append_child_value("i2c_address", f"0x{SFM_ADDRESS:02X}")
    for key, value in info_props.items():
        acq.append_child_value(key, str(value))
    chans = desc.append_child("channels")
    for label, kind, unit in (("nSeq", "counter", "samples"), ("flow", "FLOW", "slm"), ("temp", "TEMP", "degC")):
        ch = chans.append_child("channel")
        ch.append_child_value("label", label)
        ch.append_child_value("type", kind)
        ch.append_child_value("unit", unit)
    return StreamOutlet(info, chunk_size=0, max_buffered=360)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="SensorBridge COM port (default: auto-detect)")
    ap.add_argument("--rate", type=int, default=1000, help="sampling rate in Hz (default 1000, max 1000)")
    ap.add_argument("--duration", type=float, help="stop after this many seconds (default: until Ctrl+C)")
    ap.add_argument("--stop-on-eof", action="store_true", help="also stop when stdin closes (used by physio_app.py)")
    ap.add_argument("--list", action="store_true", help="print bridge, sensor and a 1 s reading, then exit")
    args = ap.parse_args()
    if not 1 <= args.rate <= 1000:
        sys.exit("--rate must be between 1 and 1000 Hz (above 1000 the bridge can't keep up reliably)")

    port = args.port or find_port()
    print(f"Connecting to SensorBridge on {port} ...", flush=True)
    bridge = Bridge(port)
    try:
        props = bridge.connect()
        print(f"Bridge: serial {props['bridge_serial']}, {props['bridge_version']}")
        print(f"Sensor: SFM3019 serial {props['sensor_serial']} on bridge port {props['bridge_port']}, "
              f"unit {props['flow_unit']}")
        if args.list:
            mean, sd, temp = bridge.read_once(1.0)
            print(f"1 s reading: flow {mean:.3f} +/- {sd:.3f} slm, temperature {temp:.1f} degC")
            if abs(mean) > 0.5:
                print("Note: flow is not zero. Fine if someone is breathing through the circuit; "
                      "otherwise check for a draught or air source at the inlet.")
            return

        outlet = make_outlet(args.rate, props)
        signal.signal(signal.SIGINT, lambda *_: setattr(bridge, "stop_requested", True))
        if args.stop_on_eof:
            def wait_for_eof():
                sys.stdin.read()
                bridge.stop_requested = True
            threading.Thread(target=wait_for_eof, daemon=True).start()

        print(f"Streaming LSL 'Flow' at {args.rate} Hz. Ctrl+C to stop.", flush=True)
        end = int(args.duration * args.rate) if args.duration else None
        try:
            bridge.run(outlet, args.rate, end)
        except SerialException:
            sys.exit("Lost the SensorBridge (USB unplugged?). Recording of flow stopped.")
        print(f"Stopped: {bridge.n_samples} samples, {bridge.n_gaps} gaps, {bridge.n_dropped} dropped.")
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
