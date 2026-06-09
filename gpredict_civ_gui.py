#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPredict <-> IC-9700 CI-V Proxy GUI (Satellite Mode)
----------------------------------------------------
Author  : BH4FUO
License : MIT License
Date    : 2025-06

Directly controls Icom IC-9700 via CI-V serial, bypassing Hamlib.
Operates the radio in ICOM Satellite Mode so wfview can show
both Main and Sub bands simultaneously.

Usage:
    python gpredict_civ_gui.py

GPredict setup:
    - Radio type: Duplex TRX
    - Device 1: localhost:4532
    - Device 2: None
"""

import sys
import time
import threading
import queue
import socketserver
import logging
import tkinter as tk
from tkinter import ttk, scrolledtext

import serial.tools.list_ports
from civ import CIVSerial, freq_to_bcd, bcd_to_freq

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("gpredict_civ_proxy")

# IC-9700 VFO selectors in satellite mode
VFO_MAIN = 0xD0
VFO_SUB = 0xD1

UPDATE_THRESHOLD_HZ = 25


def get_band(freq_hz: int | None) -> str | None:
    if freq_hz is None:
        return None
    if 144_000_000 <= freq_hz <= 148_000_000:
        return "2M"
    if 430_000_000 <= freq_hz <= 450_000_000:
        return "70CM"
    if 1_240_000_000 <= freq_hz <= 1_300_000_000:
        return "23CM"
    return None


def band_center(band: str) -> int:
    return {
        "2M": 145_900_000,
        "70CM": 435_000_000,
        "23CM": 1_295_000_000,
    }.get(band, 145_900_000)


# Standard CTCSS tone frequencies (Hz)
CTCSS_TONES = [
    "67.0", "69.3", "71.9", "74.4", "77.0", "79.7", "82.5", "85.4",
    "88.5", "91.5", "94.8", "97.4", "100.0", "103.5", "107.2", "110.9",
    "114.8", "118.8", "123.0", "127.3", "131.8", "136.5", "141.3", "146.2",
    "151.4", "156.7", "162.2", "167.9", "173.8", "179.9", "186.2", "192.8",
    "203.5", "210.7", "218.1", "225.7", "233.6", "241.8", "250.3",
]


def tone_to_bcd(tone_hz: float) -> bytes:
    """Convert CTCSS tone frequency (Hz) to 3-byte BCD for CI-V 0x1B command.
    Icom format: byte0=(1Hz,0.1Hz), byte1=(100Hz,10Hz), byte2=(10kHz,1kHz)
    """
    deci_hz = int(round(tone_hz * 10))
    d_01 = deci_hz % 10              # 0.1 Hz
    d_1 = (deci_hz // 10) % 10       # 1 Hz
    d_10 = (deci_hz // 100) % 10     # 10 Hz
    d_100 = (deci_hz // 1000) % 10   # 100 Hz
    d_1k = (deci_hz // 10000) % 10   # 1 kHz
    byte0 = (d_1 << 4) | d_01
    byte1 = (d_100 << 4) | d_10
    byte2 = (0 << 4) | d_1k
    return bytes([byte0, byte1, byte2])


class QueueHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(("log", self.format(record)))


class CIVSyncClient:
    """Thread-safe synchronous CI-V client with satellite-mode band adaptation."""

    def __init__(self, port: str, baudrate: int, ui_queue: queue.Queue):
        self.civ = CIVSerial()
        self.resp_queue = queue.Queue()
        self.lock = threading.Lock()
        self.ui_queue = ui_queue
        self.civ.set_callback(self._on_civ_msg)

        self.civ.open(port, baudrate)
        time.sleep(0.3)

        # Enable ICOM satellite mode
        self._send_raw(0x16, bytes([0x5A, 0x01]))
        time.sleep(0.1)

        logger.info("CI-V serial opened: %s @ %d baud (satellite mode ON)", port, baudrate)

    def close(self):
        # Optionally turn satellite mode off on exit
        try:
            self._send_raw(0x16, bytes([0x5A, 0x00]))
            time.sleep(0.05)
        except Exception:
            pass
        self.civ.close()

    def set_ctcss(self, vfo: int, tone_hz: float, enable: bool = True):
        """Set CTCSS tone on target VFO."""
        with self.lock:
            # Select target VFO
            self._send_raw(0x07, bytes([vfo]))
            time.sleep(0.03)
            if enable:
                # Set tone frequency (0x1B 0x00 = repeater tone / encode)
                tone_bcd = tone_to_bcd(tone_hz)
                self._send_raw(0x1B, data=bytes([0x00]) + tone_bcd)
                time.sleep(0.03)
                # Enable repeater tone
                self._send_raw(0x16, data=bytes([0x42, 0x01]))
                time.sleep(0.03)
            else:
                # Disable repeater tone
                self._send_raw(0x16, data=bytes([0x42, 0x00]))
                time.sleep(0.03)
        name = "Main" if vfo == VFO_MAIN else "Sub"
        if enable:
            self.ui_queue.put(("log", f"Set {name} CTCSS = {tone_hz} Hz"))
        else:
            self.ui_queue.put(("log", f"{name} CTCSS OFF"))

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
        return None

    def _read_freq_with_vfo(self, vfo: int) -> int | None:
        """Read frequency of a specific VFO: select it, then read current VFO."""
        self._drain_queue()
        self._send_raw(0x07, bytes([vfo]))
        time.sleep(0.03)
        self._send_raw(0x03)
        resp = self._wait_for(0x03, timeout=1.0)
        if resp is None:
            return None
        payload = resp.get("payload", b"")
        if len(payload) < 5:
            return None
        return bcd_to_freq(payload[:5])

    def _ensure_band(self, vfo: int, target_freq_hz: int):
        """Make sure the target VFO is on the right band before setting frequency."""
        target_band = get_band(target_freq_hz)
        if not target_band:
            return

        current_freq = self._read_freq_with_vfo(vfo)
        current_band = get_band(current_freq)
        if current_band == target_band:
            return

        other_vfo = VFO_SUB if vfo == VFO_MAIN else VFO_MAIN
        other_freq = self._read_freq_with_vfo(other_vfo)
        other_band = get_band(other_freq)

        if other_band == target_band:
            logger.info(
                "Band mismatch: %s is %s but needs %s; other VFO has it -> exchange",
                "Main" if vfo == VFO_MAIN else "Sub",
                current_band or "unknown",
                target_band,
            )
            self._send_raw(0x07, bytes([0xB0]))  # VFO exchange
            time.sleep(0.10)
        else:
            logger.info(
                "Band mismatch: %s is %s but needs %s -> forcing band change",
                "Main" if vfo == VFO_MAIN else "Sub",
                current_band or "unknown",
                target_band,
            )
            self._send_raw(0x07, bytes([vfo]))
            time.sleep(0.03)
            self._send_raw(0x05, freq_to_bcd(band_center(target_band)))
            time.sleep(0.10)

    def set_frequency(self, vfo: int, freq_hz: int):
        with self.lock:
            self._ensure_band(vfo, freq_hz)
            self._send_raw(0x07, bytes([vfo]))
            time.sleep(0.03)
            self._send_raw(0x05, freq_to_bcd(freq_hz))
            time.sleep(0.03)
        name = "Main" if vfo == VFO_MAIN else "Sub"
        self.ui_queue.put(("log", f"Set {name} = {freq_hz / 1e6:.6f} MHz"))

    def get_frequency(self, vfo: int) -> int | None:
        with self.lock:
            return self._read_freq_with_vfo(vfo)


class GpredictHandler(socketserver.BaseRequestHandler):
    def setup(self):
        self.radio: CIVSyncClient = self.server.radio
        self.ui_queue: queue.Queue = self.server.ui_queue
        self.swap_vfo: bool = getattr(self.server, "swap_vfo", False)
        self.uplink_hz = 0
        self.downlink_hz = 0
        self.last_uplink_hz = 0
        self.last_downlink_hz = 0
        self.recv_buf = b""
        self.ui_queue.put(("status", "GPredict connected"))

    def _vfo_downlink(self) -> int:
        return VFO_MAIN if self.swap_vfo else VFO_SUB

    def _vfo_uplink(self) -> int:
        return VFO_SUB if self.swap_vfo else VFO_MAIN

    def _freq_display_kind(self, vfo: int) -> str:
        return "main_freq" if vfo == VFO_MAIN else "sub_freq"

    def handle(self):
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
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        self.ui_queue.put(("status", "GPredict disconnected"))

    def _send(self, text: str):
        try:
            self.request.sendall((text + "\n").encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _process_line(self, line: str):
        if line.startswith("F "):
            try:
                self.downlink_hz = int(line.split()[1])
                self._apply_updates()
            except Exception:
                self._send("RPRT -1")
            return

        if line.startswith("I "):
            try:
                self.uplink_hz = int(line.split()[1])
                self._apply_updates()
            except Exception:
                self._send("RPRT -1")
            return

        if line == "f":
            vfo = self._vfo_downlink()
            freq = self.radio.get_frequency(vfo)
            if freq is not None:
                self.downlink_hz = freq
                self.last_downlink_hz = freq
                self.ui_queue.put((self._freq_display_kind(vfo), freq))
                self._send(str(freq))
            else:
                self._send("RPRT -1")
            return

        if line == "i":
            vfo = self._vfo_uplink()
            freq = self.radio.get_frequency(vfo)
            if freq is not None:
                self.uplink_hz = freq
                self.last_uplink_hz = freq
                self.ui_queue.put((self._freq_display_kind(vfo), freq))
                self._send(str(freq))
            else:
                self._send("RPRT -1")
            return

        if line == "t":
            self._send("0")
            return

        self._send("RPRT 0")

    def _apply_updates(self):
        up_changed = abs(self.uplink_hz - self.last_uplink_hz) > UPDATE_THRESHOLD_HZ
        dw_changed = abs(self.downlink_hz - self.last_downlink_hz) > UPDATE_THRESHOLD_HZ

        if not up_changed and not dw_changed:
            self._send("RPRT 0")
            return

        self.ui_queue.put(
            (
                "log",
                f"Update from GPredict: UP={self.uplink_hz / 1e6:.6f} MHz, "
                f"DW={self.downlink_hz / 1e6:.6f} MHz",
            )
        )

        if up_changed:
            vfo = self._vfo_uplink()
            self.radio.set_frequency(vfo, self.uplink_hz)
            self.last_uplink_hz = self.uplink_hz
            self.ui_queue.put((self._freq_display_kind(vfo), self.uplink_hz))

        if dw_changed:
            vfo = self._vfo_downlink()
            self.radio.set_frequency(vfo, self.downlink_hz)
            self.last_downlink_hz = self.downlink_hz
            self.ui_queue.put((self._freq_display_kind(vfo), self.downlink_hz))

        self._send("RPRT 0")


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class GPredictCIVProxyApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("GPredict <-> IC-9700 CI-V Proxy (Satellite Mode) | BH4FUO")
        self.root.geometry("720x520")
        self.root.minsize(620, 420)

        self.ui_queue = queue.Queue()
        self.radio = None
        self.server = None
        self.server_thread = None
        self.running = False

        self._build_ui()
        self._setup_logging()
        self._refresh_ports()
        self._poll_ui_queue()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        control_frame = ttk.Frame(self.root, padding=10)
        control_frame.pack(fill=tk.X)

        ttk.Label(control_frame, text="Serial Port:").grid(row=0, column=0, sticky=tk.W)
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(control_frame, textvariable=self.port_var, width=15, state="readonly")
        self.port_combo.grid(row=0, column=1, padx=5)
        ttk.Button(control_frame, text="Refresh", command=self._refresh_ports).grid(row=0, column=2, padx=5)

        ttk.Label(control_frame, text="Baud:").grid(row=0, column=3, padx=(15, 0), sticky=tk.W)
        self.baud_var = tk.StringVar(value="115200")
        ttk.Combobox(
            control_frame,
            textvariable=self.baud_var,
            values=["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"],
            width=10,
            state="readonly",
        ).grid(row=0, column=4, padx=5)

        self.start_btn = ttk.Button(control_frame, text="Start", command=self._toggle_service)
        self.start_btn.grid(row=0, column=5, padx=(15, 5))

        self.swap_var = tk.BooleanVar(value=False)
        self.swap_check = ttk.Checkbutton(
            control_frame,
            text="Swap Up/Down VFOs",
            variable=self.swap_var,
            command=self._update_labels,
        )
        self.swap_check.grid(row=0, column=6, padx=(10, 5))

        self.tone_var = tk.BooleanVar(value=False)
        self.tone_check = ttk.Checkbutton(
            control_frame,
            text="设置上行亚音",
            variable=self.tone_var,
        )
        self.tone_check.grid(row=0, column=7, padx=(10, 5))

        self.tone_freq_var = tk.StringVar(value="67.0")
        self.tone_combo = ttk.Combobox(
            control_frame,
            textvariable=self.tone_freq_var,
            values=CTCSS_TONES,
            width=8,
            state="readonly",
        )
        self.tone_combo.grid(row=0, column=8, padx=5)

        self.status_var = tk.StringVar(value="Stopped")
        self.status_lbl = ttk.Label(control_frame, textvariable=self.status_var, foreground="red")
        self.status_lbl.grid(row=0, column=9, padx=5)

        freq_frame = ttk.LabelFrame(self.root, text="Current Frequencies", padding=10)
        freq_frame.pack(fill=tk.X, padx=10, pady=5)

        self.lbl_main = ttk.Label(freq_frame, text="Main VFO (Uplink / TX):", font=("Consolas", 11))
        self.lbl_main.grid(row=0, column=0, sticky=tk.W)
        self.main_freq_var = tk.StringVar(value="---.------ MHz")
        ttk.Label(freq_frame, textvariable=self.main_freq_var, font=("Consolas", 14, "bold")).grid(
            row=0, column=1, padx=10, sticky=tk.W
        )

        self.lbl_sub = ttk.Label(freq_frame, text="Sub VFO (Downlink / RX):", font=("Consolas", 11))
        self.lbl_sub.grid(row=1, column=0, sticky=tk.W, pady=(5, 0))
        self.sub_freq_var = tk.StringVar(value="---.------ MHz")
        ttk.Label(freq_frame, textvariable=self.sub_freq_var, font=("Consolas", 14, "bold")).grid(
            row=1, column=1, padx=10, pady=(5, 0), sticky=tk.W
        )

        # Actual radio frequencies read back from the rig
        ttk.Separator(freq_frame, orient=tk.HORIZONTAL).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 5))

        ttk.Label(freq_frame, text="Main 实际频率:", font=("Consolas", 10)).grid(row=3, column=0, sticky=tk.W)
        self.main_actual_var = tk.StringVar(value="---.------ MHz")
        ttk.Label(freq_frame, textvariable=self.main_actual_var, font=("Consolas", 11), foreground="blue").grid(
            row=3, column=1, padx=10, sticky=tk.W
        )

        ttk.Label(freq_frame, text="Sub 实际频率:", font=("Consolas", 10)).grid(row=4, column=0, sticky=tk.W, pady=(3, 0))
        self.sub_actual_var = tk.StringVar(value="---.------ MHz")
        ttk.Label(freq_frame, textvariable=self.sub_actual_var, font=("Consolas", 11), foreground="blue").grid(
            row=4, column=1, padx=10, pady=(3, 0), sticky=tk.W
        )

        log_frame = ttk.LabelFrame(self.root, text="Log", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.log_text = scrolledtext.ScrolledText(log_frame, state=tk.DISABLED, wrap=tk.WORD, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _setup_logging(self):
        logger = logging.getLogger()
        logger.setLevel(logging.DEBUG)
        handler = QueueHandler(self.ui_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(handler)

    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports
        if ports:
            if not self.port_var.get() or self.port_var.get() not in ports:
                if "COM16" in ports:
                    self.port_var.set("COM16")
                else:
                    self.port_var.set(ports[0])
        else:
            self.port_var.set("")
            self._append_log("No serial ports found")

    def _update_labels(self):
        if self.swap_var.get():
            self.lbl_main.config(text="Main VFO (Downlink / RX):")
            self.lbl_sub.config(text="Sub VFO (Uplink   / TX):")
        else:
            self.lbl_main.config(text="Main VFO (Uplink / TX):")
            self.lbl_sub.config(text="Sub VFO (Downlink / RX):")

    def _toggle_service(self):
        if self.running:
            self._stop_service()
        else:
            self._start_service()

    def _start_service(self):
        port = self.port_var.get()
        if not port:
            self._append_log("Error: Please select a serial port")
            return
        try:
            baudrate = int(self.baud_var.get())
        except ValueError:
            self._append_log("Error: Invalid baud rate")
            return

        self._append_log(f"Opening CI-V on {port} @ {baudrate} baud...")

        try:
            self.radio = CIVSyncClient(port, baudrate, self.ui_queue)
        except Exception as exc:
            self._append_log(f"Failed to open serial port: {exc}")
            return

        try:
            self.server = ThreadedTCPServer(("127.0.0.1", 4532), GpredictHandler)
            self.server.radio = self.radio
            self.server.ui_queue = self.ui_queue
            self.server.swap_vfo = self.swap_var.get()
        except Exception as exc:
            self._append_log(f"Failed to start TCP server: {exc}")
            self.radio.close()
            self.radio = None
            return

        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

        self.running = True

        self.poll_thread = threading.Thread(target=self._poll_radio_freq, daemon=True)
        self.poll_thread.start()
        self.start_btn.config(text="Stop")
        self.status_var.set(f"Running on {port} @ {baudrate}")
        self._set_status_color("green")
        self._append_log("Listening on 127.0.0.1:4532 for GPredict")
        self._append_log("Mode: ICOM Satellite Mode with auto band adaptation")
        mapping = "Uplink->Main, Downlink->Sub" if not self.swap_var.get() else "Uplink->Sub, Downlink->Main"
        self._append_log(f"VFO mapping: {mapping}")

        if self.tone_var.get():
            try:
                tone_hz = float(self.tone_freq_var.get())
                uplink_vfo = VFO_MAIN if not self.swap_var.get() else VFO_SUB
                self.radio.set_ctcss(uplink_vfo, tone_hz, enable=True)
            except Exception as exc:
                self._append_log(f"Failed to set CTCSS: {exc}")

    def _stop_service(self):
        self._append_log("Stopping service...")
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception as exc:
                self._append_log(f"Server shutdown error: {exc}")
            self.server = None

        if self.radio:
            try:
                self.radio.close()
            except Exception as exc:
                self._append_log(f"Radio close error: {exc}")
            self.radio = None

        self.running = False
        self.start_btn.config(text="Start")
        self.status_var.set("Stopped")
        self._set_status_color("red")
        self._append_log("Service stopped")
        self.main_actual_var.set("---.------ MHz")
        self.sub_actual_var.set("---.------ MHz")

    def _set_status_color(self, color: str):
        self.status_lbl.config(foreground=color)

    def _append_log(self, text: str):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _update_freq_display(self, kind: str, freq_hz: int):
        text = f"{freq_hz / 1e6:.6f} MHz"
        if kind == "main_freq":
            self.main_freq_var.set(text)
        elif kind == "sub_freq":
            self.sub_freq_var.set(text)

    def _poll_ui_queue(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind in ("main_freq", "sub_freq"):
                    self._update_freq_display(kind, payload)
                elif kind == "main_actual":
                    self.main_actual_var.set(f"{payload / 1e6:.6f} MHz")
                elif kind == "sub_actual":
                    self.sub_actual_var.set(f"{payload / 1e6:.6f} MHz")
                elif kind == "status":
                    self.status_var.set(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_ui_queue)

    def _poll_radio_freq(self):
        while True:
            if not self.running:
                break
            if self.radio:
                try:
                    main_freq = self.radio.get_frequency(VFO_MAIN)
                    sub_freq = self.radio.get_frequency(VFO_SUB)
                    if main_freq is not None:
                        self.ui_queue.put(("main_actual", main_freq))
                    if sub_freq is not None:
                        self.ui_queue.put(("sub_actual", sub_freq))
                except Exception:
                    pass
            time.sleep(1.5)

    def _on_close(self):
        self._stop_service()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = GPredictCIVProxyApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
