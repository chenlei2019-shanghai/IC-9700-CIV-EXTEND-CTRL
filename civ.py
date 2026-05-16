"""
IC-9700 CI-V Protocol Handler
Handles packing/unpacking of CI-V commands according to Icom's protocol.
"""

import serial
import serial.tools.list_ports
import threading
import time
import struct

# IC-9700 default CI-V address
RADIO_ADDR = 0xA2
CONTROLLER_ADDR = 0xE0

PREAMBLE = bytes([0xFE, 0xFE])
END_CODE = 0xFD
OK_CODE = 0xFB
NG_CODE = 0xFA

# Operating modes
MODES = {
    0x00: "LSB",
    0x01: "USB",
    0x02: "AM",
    0x03: "CW",
    0x04: "RTTY",
    0x05: "FM",
    0x07: "CW-R",
    0x08: "RTTY-R",
    0x17: "DV",
    0x22: "DD",
}

MODES_REV = {v: k for k, v in MODES.items()}

FILTERS = {
    0x01: "FIL1",
    0x02: "FIL2",
    0x03: "FIL3",
}


def freq_to_bcd(freq_hz: int) -> bytes:
    """Convert frequency in Hz to 5-byte BCD (little-endian nibble order)."""
    # IC-9700 frequency format: 10 bytes, each byte = two BCD digits
    # Order: 10Hz, 1Hz, 1kHz, 100Hz, 100kHz, 10kHz, 10MHz, 1MHz, 1GHz, 100MHz
    freq_str = f"{freq_hz:010d}"
    # Pad to 10 digits
    if len(freq_str) < 10:
        freq_str = "0" * (10 - len(freq_str)) + freq_str
    # Build bytes in CI-V order
    result = bytearray(5)
    pairs = [
        (freq_str[8], freq_str[9]),   # 10Hz, 1Hz -> byte 0
        (freq_str[6], freq_str[7]),   # 1kHz, 100Hz -> byte 1
        (freq_str[4], freq_str[5]),   # 100kHz, 10kHz -> byte 2
        (freq_str[2], freq_str[3]),   # 10MHz, 1MHz -> byte 3
        (freq_str[0], freq_str[1]),   # 1GHz, 100MHz -> byte 4
    ]
    for i, (high, low) in enumerate(pairs):
        result[i] = (int(high) << 4) | int(low)
    return bytes(result)


def bcd_to_freq(bcd: bytes) -> int:
    """Convert 5-byte BCD to frequency in Hz.

    Matches freq_to_bcd encoding (little-endian nibble order):
      byte 0: 10Hz, 1Hz
      byte 1: 1kHz, 100Hz
      byte 2: 100kHz, 10kHz
      byte 3: 10MHz, 1MHz
      byte 4: 1GHz, 100MHz
    """
    if len(bcd) < 5:
        return 0
    freq = 0
    # byte 0
    freq += ((bcd[0] >> 4) & 0x0F) * 10
    freq += (bcd[0] & 0x0F) * 1
    # byte 1
    freq += ((bcd[1] >> 4) & 0x0F) * 1000
    freq += (bcd[1] & 0x0F) * 100
    # byte 2
    freq += ((bcd[2] >> 4) & 0x0F) * 100000
    freq += (bcd[2] & 0x0F) * 10000
    # byte 3
    freq += ((bcd[3] >> 4) & 0x0F) * 10000000
    freq += (bcd[3] & 0x0F) * 1000000
    # byte 4
    freq += ((bcd[4] >> 4) & 0x0F) * 1000000000
    freq += (bcd[4] & 0x0F) * 100000000
    return freq


def build_command(cmd: int, subcmd: int = None, data: bytes = None, to_addr: int = None, from_addr: int = None) -> bytes:
    """Build a CI-V command frame."""
    msg = bytearray()
    msg.extend(PREAMBLE)
    msg.append(to_addr if to_addr is not None else RADIO_ADDR)
    msg.append(from_addr if from_addr is not None else CONTROLLER_ADDR)
    msg.append(cmd)
    if subcmd is not None:
        msg.append(subcmd)
    if data:
        msg.extend(data)
    msg.append(END_CODE)
    return bytes(msg)


def parse_response(data: bytes) -> dict:
    """Parse a CI-V response frame.

    Accepts broadcast address 0x00 (used in Icom LAN CI-V tunnel).
    """
    if len(data) < 6:
        return None
    if data[0:2] != PREAMBLE:
        return None
    # Check direction: from radio to controller (accept broadcast 0x00 in LAN mode)
    to_addr = data[2]
    from_addr = data[3]
    if to_addr not in (CONTROLLER_ADDR, 0x00) or from_addr not in (RADIO_ADDR, 0x00):
        return None
    cmd = data[4]
    payload = data[5:-1] if data[-1] == END_CODE else data[5:]
    return {"cmd": cmd, "payload": payload}


class CIVSerial:
    def __init__(self):
        self.ser = None
        self.lock = threading.Lock()
        self.read_thread = None
        self.running = False
        self.callback = None
        self._pending = {}
        self._pending_lock = threading.Lock()
        self._seq = 0

    def list_ports(self):
        return [p.device for p in serial.tools.list_ports.comports()]

    def open(self, port: str, baudrate: int = 115200, timeout: float = 0.1):
        self.close()
        self.ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout,
            write_timeout=1,
        )
        self.running = True
        self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.read_thread.start()
        return True

    def close(self):
        self.running = False
        if self.read_thread:
            self.read_thread.join(timeout=1)
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.ser = None

    def is_open(self):
        return self.ser is not None and self.ser.is_open

    def set_callback(self, callback):
        self.callback = callback

    def _read_loop(self):
        buffer = bytearray()
        while self.running:
            try:
                if self.ser and self.ser.is_open:
                    chunk = self.ser.read(256)
                    if chunk:
                        buffer.extend(chunk)
                        # Process complete frames
                        while True:
                            idx = buffer.find(0xFD)
                            if idx == -1:
                                break
                            frame = bytes(buffer[:idx+1])
                            buffer = buffer[idx+1:]
                            self._handle_frame(frame)
                else:
                    time.sleep(0.01)
            except Exception as e:
                time.sleep(0.01)

    def _handle_frame(self, frame: bytes):
        parsed = parse_response(frame)
        if parsed is None:
            # Check for OK/NG (accept broadcast 0x00 in LAN mode)
            if len(frame) >= 6 and frame[0:2] == PREAMBLE:
                to_addr = frame[2]
                from_addr = frame[3]
                if to_addr in (CONTROLLER_ADDR, 0x00) and from_addr in (RADIO_ADDR, 0x00):
                    if frame[4] == OK_CODE:
                        self._resolve_pending("ok", True)
                        if self.callback:
                            self.callback({"type": "ok"})
                    elif frame[4] == NG_CODE:
                        self._resolve_pending("ng", True)
                        if self.callback:
                            self.callback({"type": "ng"})
            return
        if self.callback:
            self.callback({"type": "data", **parsed})

    def _resolve_pending(self, key, value):
        with self._pending_lock:
            if key in self._pending:
                self._pending[key] = value

    def send_raw(self, data: bytes):
        with self.lock:
            if self.ser and self.ser.is_open:
                self.ser.write(data)
                self.ser.flush()
                return True
        return False

    def send(self, cmd: int, subcmd: int = None, data: bytes = None) -> bool:
        frame = build_command(cmd, subcmd, data)
        return self.send_raw(frame)

    def transact(self, cmd: int, subcmd: int = None, data: bytes = None, timeout: float = 0.5) -> dict:
        """Send a command and wait for response."""
        seq = self._seq
        self._seq += 1
        key = f"resp_{seq}"
        with self._pending_lock:
            self._pending[key] = None

        frame = build_command(cmd, subcmd, data)
        self.send_raw(frame)

        start = time.time()
        while time.time() - start < timeout:
            with self._pending_lock:
                # This is a simplified approach; in real use we'd match by cmd
                pass
            time.sleep(0.01)
        return None


class CIVController:
    """High-level controller for IC-9700 CI-V commands."""
    def __init__(self, transport):
        self.ser = transport

    # --- Basic Operations ---
    def set_frequency(self, freq_hz: int):
        return self.ser.send(0x05, data=freq_to_bcd(freq_hz))

    def read_frequency(self):
        return self.ser.send(0x03)

    def set_mode(self, mode: int, filter_setting: int = 0x01):
        data = bytes([mode, filter_setting])
        return self.ser.send(0x06, data=data)

    def read_mode(self):
        return self.ser.send(0x04)

    # --- VFO ---
    def select_vfo(self, vfo: int):
        return self.ser.send(0x07, data=bytes([vfo]))

    def vfo_a(self): return self.select_vfo(0x00)
    def vfo_b(self): return self.select_vfo(0x01)
    def vfo_equal(self): return self.select_vfo(0xA0)
    def vfo_exchange(self): return self.select_vfo(0xB0)
    def select_main(self): return self.select_vfo(0xD0)
    def select_sub(self): return self.select_vfo(0xD1)

    def read_band_selection(self, band: int = 0x00):
        return self.ser.send(0x07, data=bytes([0xD2, band]))

    # --- Memory ---
    def select_memory(self, channel: int):
        ch_high = (channel >> 8) & 0xFF
        ch_low = channel & 0xFF
        return self.ser.send(0x08, data=bytes([ch_high, ch_low]))

    def memory_write(self):
        return self.ser.send(0x09)

    def memory_copy_vfo(self):
        return self.ser.send(0x0A)

    def memory_clear(self):
        return self.ser.send(0x0B)

    # --- Split / Duplex ---
    def set_split(self, on: bool):
        return self.ser.send(0x0F, data=bytes([0x01 if on else 0x00]))

    def read_split(self):
        return self.ser.send(0x0F)

    def set_duplex(self, duplex: int):
        return self.ser.send(0x0F, data=bytes([duplex]))

    # --- Scan ---
    def scan(self, scan_type: int):
        return self.ser.send(0x0E, data=bytes([scan_type]))

    def scan_cancel(self): return self.scan(0x00)
    def scan_programmed_memory(self): return self.scan(0x01)
    def scan_programmed(self): return self.scan(0x02)
    def scan_delta_f(self): return self.scan(0x03)
    def scan_fine_programmed(self): return self.scan(0x12)
    def scan_fine_delta(self): return self.scan(0x13)
    def scan_memory(self): return self.scan(0x22)
    def scan_select_memory(self): return self.scan(0x23)
    def scan_mode_select(self): return self.scan(0x24)

    def set_scan_span(self, span: int):
        return self.ser.send(0x0E, data=bytes([0xA0 + span]))

    def set_scan_resume(self, on: bool):
        return self.ser.send(0x0E, data=bytes([0xD3 if on else 0xD0]))

    # --- Tuning Step ---
    def set_tuning_step(self, step: int):
        return self.ser.send(0x10, data=bytes([step]))

    def read_tuning_step(self):
        return self.ser.send(0x10)

    # --- Attenuator ---
    def set_attenuator(self, att: int):
        return self.ser.send(0x11, data=bytes([att]))

    def read_attenuator(self):
        return self.ser.send(0x11)

    # --- Levels (0x14 subcmd) ---
    def set_level(self, subcmd: int, value: int):
        data = bytes([subcmd, (value >> 8) & 0xFF, value & 0xFF])
        return self.ser.send(0x14, data=data)

    def read_level(self, subcmd: int):
        return self.ser.send(0x14, data=bytes([subcmd]))

    def set_af_level(self, v): return self.set_level(0x01, v)
    def set_rf_gain(self, v): return self.set_level(0x02, v)
    def set_squelch(self, v): return self.set_level(0x03, v)
    def set_nr_level(self, v): return self.set_level(0x06, v)
    def set_pbt1(self, v): return self.set_level(0x07, v)
    def set_pbt2(self, v): return self.set_level(0x08, v)
    def set_cw_pitch(self, v): return self.set_level(0x09, v)
    def set_rf_power(self, v): return self.set_level(0x0A, v)
    def set_mic_gain(self, v): return self.set_level(0x0B, v)
    def set_key_speed(self, v): return self.set_level(0x0C, v)
    def set_notch(self, v): return self.set_level(0x0D, v)
    def set_comp_level(self, v): return self.set_level(0x0E, v)
    def set_break_in_delay(self, v): return self.set_level(0x0F, v)
    def set_nb_level(self, v): return self.set_level(0x12, v)
    def set_monitor_level(self, v): return self.set_level(0x15, v)
    def set_vox_gain(self, v): return self.set_level(0x16, v)
    def set_anti_vox(self, v): return self.set_level(0x17, v)
    def set_backlight(self, v): return self.set_level(0x19, v)

    # --- Read Meters (0x15 subcmd) ---
    def read_meter(self, subcmd: int):
        return self.ser.send(0x15, data=bytes([subcmd]))

    def read_squelch_status(self): return self.read_meter(0x01)
    def read_smeter(self): return self.read_meter(0x02)
    def read_po_meter(self): return self.read_meter(0x11)
    def read_swr_meter(self): return self.read_meter(0x12)
    def read_alc_meter(self): return self.read_meter(0x13)
    def read_comp_meter(self): return self.read_meter(0x14)
    def read_vd_meter(self): return self.read_meter(0x15)
    def read_id_meter(self): return self.read_meter(0x16)
    def read_tx_power_setting(self):
        return self.ser.send(0x24, data=bytes([0x00]))

    # --- Functions (0x16 subcmd) ---
    def set_function(self, subcmd: int, value: int):
        data = bytes([subcmd, value])
        return self.ser.send(0x16, data=data)

    def read_function(self, subcmd: int):
        return self.ser.send(0x16, data=bytes([subcmd]))

    def set_preamp(self, v): return self.set_function(0x02, v)
    def set_agc(self, v): return self.set_function(0x12, v)
    def set_nb(self, v): return self.set_function(0x22, v)
    def set_nr(self, v): return self.set_function(0x40, v)
    def set_auto_notch(self, v): return self.set_function(0x41, v)
    def set_repeater_tone(self, v): return self.set_function(0x42, v)
    def set_tone_squelch(self, v): return self.set_function(0x43, v)
    def set_speech_compressor(self, v): return self.set_function(0x44, v)
    def set_monitor(self, v): return self.set_function(0x45, v)
    def set_vox(self, v): return self.set_function(0x46, v)
    def set_bkin(self, v): return self.set_function(0x47, v)
    def set_manual_notch(self, v): return self.set_function(0x48, v)
    def set_afc(self, v): return self.set_function(0x4A, v)
    def set_dtcs(self, v): return self.set_function(0x4B, v)
    def set_twin_peak_filter(self, v): return self.set_function(0x4F, v)
    def set_dial_lock(self, v): return self.set_function(0x50, v)
    def set_dsp_if_filter(self, v): return self.set_function(0x56, v)
    def set_manual_notch_width(self, v): return self.set_function(0x57, v)
    def set_ssb_tx_bandwidth(self, v): return self.set_function(0x58, v)
    def set_sub_band(self, v): return self.set_function(0x59, v)
    def set_satellite_mode(self, v): return self.set_function(0x5A, v)
    def set_dsql_csql(self, v): return self.set_function(0x5B, v)
    def set_gps_tx_mode(self, v): return self.set_function(0x5C, v)
    def set_tone_squelch_func(self, v): return self.set_function(0x5C, v)  # Same cmd different range
    def set_ip_plus(self, v): return self.set_function(0x65, v)

    # --- Power ---
    def power_off(self):
        return self.ser.send(0x18, data=bytes([0x00]))

    def power_on(self):
        # Requires multiple FE preamble
        preamble = bytes([0xFE] * 5)
        frame = preamble + build_command(0x18, data=bytes([0x01]))
        return self.ser.send_raw(frame)

    def read_id(self):
        return self.ser.send(0x19, data=bytes([0x00]))

    # --- RIT ---
    def set_rit_freq(self, freq_hz: int, direction: int = 0x00):
        # freq in Hz, max 9999 Hz? Actually BCD 4 digits
        s = f"{abs(freq_hz):04d}"
        bcd = bytearray(3)
        bcd[0] = (int(s[2]) << 4) | int(s[3])  # 10Hz, 1Hz
        bcd[1] = (int(s[0]) << 4) | int(s[1])  # 1kHz, 100Hz
        bcd[2] = direction  # 00=+, 01=-
        return self.ser.send(0x21, data=bytes(bcd))

    def set_rit(self, on: bool):
        return self.ser.send(0x21, data=bytes([0x01, 0x01 if on else 0x00]))

    # --- TX Control ---
    def set_tx_power_setting(self, on: bool):
        return self.ser.send(0x24, data=bytes([0x00, 0x01 if on else 0x00]))

    def read_tx_power_setting(self):
        return self.ser.send(0x24, data=bytes([0x00]))

    # --- Transceiver Status ---
    def read_tx_status(self):
        return self.ser.send(0x1C, data=bytes([0x00]))

    def read_xfc(self):
        return self.ser.send(0x1C, data=bytes([0x02]))

    def set_xfc(self, on: bool):
        return self.ser.send(0x1C, data=bytes([0x02, 0x01 if on else 0x00]))

    # --- 1A Commands (Extended settings) ---
    def send_1a(self, subcmd1: int, subcmd2: int = None, data: bytes = None):
        payload = bytearray([subcmd1])
        if subcmd2 is not None:
            payload.extend([(subcmd2 >> 8) & 0xFF, subcmd2 & 0xFF])
        if data:
            payload.extend(data)
        return self.ser.send(0x1A, data=bytes(payload))

    def read_1a_05(self, item: int):
        return self.send_1a(0x05, item)

    def set_1a_05(self, item: int, value: bytes):
        return self.send_1a(0x05, item, value)

    def read_1a_05_value(self, item: int):
        return self.send_1a(0x05, item)

    def set_lcd_backlight(self, v: int):
        return self.set_1a_05(0x0152, bytes([(v >> 8) & 0xFF, v & 0xFF]))

    def set_display_type(self, v: int):
        return self.set_1a_05(0x0153, bytes([v]))

    def set_beep_level(self, v: int):
        return self.set_1a_05(0x0027, bytes([(v >> 8) & 0xFF, v & 0xFF]))

    def set_vox_delay(self, v: int):
        return self.set_1a_05(0x0330, bytes([v]))

    # --- 1B Commands (Tone/DTCS) ---
    def read_repeater_tone(self):
        return self.ser.send(0x1B, data=bytes([0x00]))

    def read_tsql_tone(self):
        return self.ser.send(0x1B, data=bytes([0x01]))

    def read_dtcs(self):
        return self.ser.send(0x1B, data=bytes([0x02]))

    # --- Voice TX ---
    def voice_tx_memory(self, ch: int):
        return self.ser.send(0x28, data=bytes([ch]))

