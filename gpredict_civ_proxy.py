#!/usr/bin/env python3
"""
GPredict -> IC-9700 CI-V Direct Proxy
-------------------------------------
Bypasses Hamlib/rigctld entirely. Listens on TCP 4532 like rigctld,
but directly controls the IC-9700 via serial CI-V.

Usage:
    python gpredict_civ_proxy.py COM16 115200

GPredict setup:
    - Radio type: Duplex TRX
    - Device 1: localhost:4532 (used for both up/down by this proxy)
    - Device 2: None
    - VFO mapping: ICOM satellite mode default
        Uplink   (GPredict 'I') -> Main VFO (0xD0)
        Downlink (GPredict 'F') -> Sub  VFO (0xD1)
"""

import sys
import time
import threading
import queue
import socketserver
import logging

from civ import CIVSerial, freq_to_bcd, bcd_to_freq

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("gpredict_civ_proxy")

# IC-9700 VFO selectors
VFO_MAIN = 0xD0
VFO_SUB = 0xD1

# Only update radio when frequency shift exceeds this (Hz)
UPDATE_THRESHOLD_HZ = 25


class CIVSyncClient:
    """Thread-safe synchronous wrapper around CIVSerial."""

    def __init__(self, port: str, baudrate: int = 115200):
        self.civ = CIVSerial()
        self.resp_queue = queue.Queue()
        self.lock = threading.Lock()
        self.civ.set_callback(self._on_civ_msg)

        try:
            self.civ.open(port, baudrate)
        except Exception as exc:
            raise RuntimeError(f"Cannot open {port} @ {baudrate}: {exc}")

        time.sleep(0.3)  # let reader thread start
        logger.info("CI-V serial opened: %s @ %d baud", port, baudrate)

        # Enter ICOM satellite mode so Main/Sub are both active
        self._send_raw(0x16, bytes([0x5A, 0x01]))
        time.sleep(0.1)

    def close(self):
        self.civ.close()

    def _on_civ_msg(self, msg):
        self.resp_queue.put(msg)

    def _drain_queue(self):
        while not self.resp_queue.empty():
            try:
                self.resp_queue.get_nowait()
            except queue.Empty:
                break

    def _send_raw(self, cmd: int, data: bytes = None):
        self.civ.send(cmd, data=data)

    def _wait_for(self, expected_cmd: int, timeout: float = 1.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self.resp_queue.get(timeout=0.02)
            except queue.Empty:
                continue
            if msg.get("type") == "data" and msg.get("cmd") == expected_cmd:
                return msg
            # OK/NG messages are ignored here; they are logged by CIVSerial
        return None

    def set_frequency(self, vfo: int, freq_hz: int):
        """Select target VFO, then set its frequency with 0x05."""
        with self.lock:
            # 1) select VFO
            self._send_raw(0x07, bytes([vfo]))
            time.sleep(0.05)
            # 2) set frequency
            self._send_raw(0x05, freq_to_bcd(freq_hz))
            time.sleep(0.05)
        logger.info("Set %s = %.6f MHz", "Main" if vfo == VFO_MAIN else "Sub", freq_hz / 1e6)

    def get_frequency(self, vfo: int) -> int | None:
        """Select target VFO, then read its frequency with 0x03."""
        with self.lock:
            self._drain_queue()
            self._send_raw(0x07, bytes([vfo]))
            time.sleep(0.05)
            self._send_raw(0x03)
            resp = self._wait_for(0x03, timeout=1.0)

        if resp is None:
            logger.warning("No response reading %s frequency", "Main" if vfo == VFO_MAIN else "Sub")
            return None

        payload = resp.get("payload", b"")
        if len(payload) < 5:
            logger.warning("Short frequency response: %s", payload.hex())
            return None

        freq = bcd_to_freq(payload[:5])
        logger.debug("Read %s = %.6f MHz", "Main" if vfo == VFO_MAIN else "Sub", freq / 1e6)
        return freq


class GpredictHandler(socketserver.BaseRequestHandler):
    def setup(self):
        self.radio: CIVSyncClient = self.server.radio
        self.uplink_hz = 0
        self.downlink_hz = 0
        self.last_uplink_hz = 0
        self.last_downlink_hz = 0
        self.recv_buf = b""

    def handle(self):
        logger.info("GPredict connected from %s", self.client_address)
        try:
            while True:
                data = self.request.recv(1024)
                if not data:
                    break
                self.recv_buf += data
                while b"\n" in self.recv_buf:
                    idx = self.recv_buf.find(b"\n")
                    line = self.recv_buf[:idx].decode("utf-8", errors="ignore").strip()
                    self.recv_buf = self.recv_buf[idx + 1 :]
                    if line:
                        self._process_line(line)
        except (ConnectionResetError, BrokenPipeError):
            pass
        logger.info("GPredict disconnected")

    def _send(self, text: str):
        try:
            self.request.sendall((text + "\n").encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _process_line(self, line: str):
        logger.debug("GPredict -> %s", line)

        # F: GPredict sets DOWNLINK frequency
        if line.startswith("F "):
            try:
                self.downlink_hz = int(line.split()[1])
                self._apply_updates()
            except Exception:
                self._send("RPRT -1")
            return

        # I: GPredict sets UPLINK frequency
        if line.startswith("I "):
            try:
                self.uplink_hz = int(line.split()[1])
                self._apply_updates()
            except Exception:
                self._send("RPRT -1")
            return

        # f: GPredict reads DOWNLINK frequency -> Sub VFO
        if line == "f":
            freq = self.radio.get_frequency(VFO_SUB)
            if freq is not None:
                self.downlink_hz = freq
                self.last_downlink_hz = freq
                self._send(str(freq))
            else:
                self._send("RPRT -1")
            return

        # i: GPredict reads UPLINK frequency -> Main VFO
        if line == "i":
            freq = self.radio.get_frequency(VFO_MAIN)
            if freq is not None:
                self.uplink_hz = freq
                self.last_uplink_hz = freq
                self._send(str(freq))
            else:
                self._send("RPRT -1")
            return

        # t: PTT status query
        if line == "t":
            self._send("0")
            return

        # Everything else: pretend success so GPredict doesn't abort
        self._send("RPRT 0")

    def _apply_updates(self):
        up_changed = abs(self.uplink_hz - self.last_uplink_hz) > UPDATE_THRESHOLD_HZ
        dw_changed = abs(self.downlink_hz - self.last_downlink_hz) > UPDATE_THRESHOLD_HZ

        if not up_changed and not dw_changed:
            self._send("RPRT 0")
            return

        logger.info(
            "Update radio: uplink=%.6f MHz, downlink=%.6f MHz",
            self.uplink_hz / 1e6,
            self.downlink_hz / 1e6,
        )

        if up_changed:
            self.radio.set_frequency(VFO_MAIN, self.uplink_hz)
            self.last_uplink_hz = self.uplink_hz

        if dw_changed:
            self.radio.set_frequency(VFO_SUB, self.downlink_hz)
            self.last_downlink_hz = self.downlink_hz

        self._send("RPRT 0")


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM16"
    baudrate = int(sys.argv[2]) if len(sys.argv) > 2 else 115200

    logger.info("Starting GPredict <-> IC-9700 CI-V proxy")
    logger.info("Serial: %s @ %d baud", port, baudrate)

    radio = CIVSyncClient(port, baudrate)

    server = ThreadedTCPServer(("127.0.0.1", 4532), GpredictHandler)
    server.radio = radio

    logger.info("Listening on 127.0.0.1:4532 for GPredict...")
    logger.info("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        server.shutdown()
        server.server_close()
        radio.close()


if __name__ == "__main__":
    main()
