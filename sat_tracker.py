"""
Software satellite Doppler tracker for IC-9700 — dual-VFO independent tracking.

Design (same approach as the CSN S.A.T. hardware controller, pure software):

- The radio stays in normal VFO mode. Satellite mode is forced OFF
  (CI-V 16 5A 00) because its CI-V VFO semantics are broken/limited
  (07 01 -> NG, 25/26 -> NG in satellite mode).
- Dualwatch ON (16 59 01): MAIN and SUB bands both receive.
- MAIN band = UPLINK (TX), SUB band = DOWNLINK (RX).
  In normal VFO mode the IC-9700 always transmits on MAIN, so the uplink
  must live on MAIN. (The S.A.T. manual says the same: "always use VFO
  mode... the red TX icon will be above the top VFO".)
- Sub Band Mute (TX) speaker output is turned OFF (1A 05 0033 00) so the
  downlink stays audible while transmitting (full-duplex self monitoring).
- Per-band control: 07 D0/D1 selects MAIN/SUB for CI-V, then 05 (freq) /
  06 (mode) / 1B+16 42 (CTCSS) apply to that band.

Doppler model (skyfield, TLE):
  range_rate v > 0  <=> satellite receding
  downlink RX tuning : f_down * (C - v) / C
  uplink   TX tuning : f_up   * C / (C - v)      (pre-compensation, opposite sign)

Note: sat.py used the downlink formula for the uplink as well (sign error);
this module computes both correctly.
"""

import logging
import threading
import time

logger = logging.getLogger("sat")

C = 299792458.0  # speed of light, m/s


def _timescale():
    """Timescale using the leap-second table bundled inside skyfield, so the
    packaged EXE works offline (load.timescale() would otherwise try to
    download Leap_Second.dat on first use)."""
    from skyfield.api import load
    try:
        return load.timescale(builtin=True)
    except TypeError:  # older skyfield without builtin=
        return load.timescale()

# ---------------------------------------------------------------------------
# Transponder presets (nominal center frequencies; verify against
# SatNOGS / AMSAT before a pass — all values are editable in the UI)
# ---------------------------------------------------------------------------
CTCSS_TONES = [
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4,
    100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5,
    141.3, 146.2, 151.4, 156.7, 162.2, 167.9, 173.8, 179.9, 186.2, 192.8,
    203.5, 210.7, 218.1, 225.7, 233.6, 241.8, 250.3,
]

SATELLITE_PRESETS = {
    # --- FM birds ---
    "ISS (VOICE)": dict(up=145_990_000, up_mode="FM", tone=67.0,
                        down=437_800_000, down_mode="FM", invert=False),
    "SO-50":       dict(up=145_850_000, up_mode="FM", tone=67.0,
                        down=436_795_000, down_mode="FM", invert=False),
    "AO-91":       dict(up=435_250_000, up_mode="FM", tone=67.0,
                        down=145_960_000, down_mode="FM", invert=False),
    "AO-85":       dict(up=435_170_000, up_mode="FM", tone=67.0,
                        down=145_980_000, down_mode="FM", invert=False),
    # --- Linear transponders (centers; tune across the passband) ---
    "FO-29":       dict(up=145_950_000, up_mode="LSB", tone=None,
                        down=435_850_000, down_mode="USB", invert=True),
    "RS-44":       dict(up=145_965_000, up_mode="LSB", tone=None,
                        down=435_640_000, down_mode="USB", invert=True),
    "CAS-4A":      dict(up=435_050_000, up_mode="LSB", tone=None,
                        down=145_955_000, down_mode="USB", invert=True),
    "CAS-4B":      dict(up=435_160_000, up_mode="LSB", tone=None,
                        down=145_895_000, down_mode="USB", invert=True),
    "AO-7":        dict(up=432_150_000, up_mode="LSB", tone=None,
                        down=145_950_000, down_mode="USB", invert=False),  # non-inverting!
}

MODE_CODES = {"LSB": 0x00, "USB": 0x01, "AM": 0x02, "CW": 0x03,
              "RTTY": 0x04, "FM": 0x05, "CW-R": 0x07, "RTTY-R": 0x08,
              "DV": 0x17, "DD": 0x22}

# IC-9700 SUB band covers 144/430 MHz only (no 23 cm on SUB).
SUB_BAND_MIN_HZ = 100_000_000
SUB_BAND_MAX_HZ = 500_000_000

# Band table: name, low, high, center (used for band-change staging).
# IC-9700 rule (basic manual): "The same band cannot be set to both Main and
# Sub bands" — a 0x05 write that would create a same-band pair is NG'd, which
# is exactly what we observed on the real radio. Band changes therefore stage
# through 0x07 0xB0 (MAIN/SUB exchange) or a band-center write in a conflict
# -free order.
BANDS = [
    ("2M",   144_000_000,   148_000_000,   145_900_000),
    ("70CM", 430_000_000,   450_000_000,   435_000_000),
    ("23CM", 1_240_000_000, 1_300_000_000, 1_295_000_000),
]


def band_of(freq_hz):
    for name, lo, hi, _c in BANDS:
        if lo <= freq_hz <= hi:
            return name
    return None


def band_center(name):
    for n, _lo, _hi, c in BANDS:
        if n == name:
            return c
    return 145_900_000


def _data_path(filename: str) -> str:
    """Persistent data file location: next to the EXE when frozen."""
    import os
    import sys
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


TLE_MAX_AGE_DAYS = 7  # UI warns when the TLE cache is older than this


def grid_to_latlon(grid: str):
    """Maidenhead grid locator (4/6/8 chars) -> (lat, lon) of square center."""
    g = grid.strip().upper()
    if len(g) < 4 or len(g) % 2 or len(g) > 8:
        raise ValueError("网格格式不正确（4/6/8 位，如 PM01 或 PM01PE）")
    if not ("A" <= g[0] <= "R" and "A" <= g[1] <= "R"):
        raise ValueError("网格前两位须为 A-R 字母")
    lon = (ord(g[0]) - ord("A")) * 20 - 180
    lat = (ord(g[1]) - ord("A")) * 10 - 90
    if not (g[2].isdigit() and g[3].isdigit()):
        raise ValueError("网格第 3/4 位须为数字")
    lon += int(g[2]) * 2
    lat += int(g[3]) * 1
    if len(g) == 4:
        return lat + 0.5, lon + 1.0
    if not ("A" <= g[4] <= "X" and "A" <= g[5] <= "X"):
        raise ValueError("网格第 5/6 位须为 A-X 字母")
    lon += (ord(g[4]) - ord("A")) * (5.0 / 60)
    lat += (ord(g[5]) - ord("A")) * (2.5 / 60)
    if len(g) == 6:
        return lat + 2.5 / 120, lon + 5.0 / 120
    if not (g[6].isdigit() and g[7].isdigit()):
        raise ValueError("网格第 7/8 位须为数字")
    lon += int(g[6]) * (5.0 / 600)
    lat += int(g[7]) * (2.5 / 600)
    return lat + 2.5 / 1200, lon + 5.0 / 1200


def tone_to_bcd(tone_hz: float) -> bytes:
    """CTCSS tone (Hz) -> 3-byte BCD for CI-V 1B 00.
    byte0 = (1 Hz, 0.1 Hz), byte1 = (100 Hz, 10 Hz), byte2 = (10 kHz, 1 kHz)."""
    deci = int(round(tone_hz * 10))
    d01 = deci % 10
    d1 = (deci // 10) % 10
    d10 = (deci // 100) % 10
    d100 = (deci // 1000) % 10
    d1k = (deci // 10000) % 10
    d10k = (deci // 100000) % 10
    return bytes([(d1 << 4) | d01, (d100 << 4) | d10, (d10k << 4) | d1k])


def fetch_tle_celestrak(url: str = None, timeout: int = 15) -> dict:
    """Fetch TLE data from a URL (default: CelesTrak amateur group).
    Returns {name: (line1, line2)}. Uses stdlib urllib."""
    import urllib.request
    if not url:
        url = "https://celestrak.org/NORAD/elements/gp.php?GROUP=amateur&FORMAT=tle"
    req = urllib.request.Request(url, headers={"User-Agent": "ic9700-civ-ctrl/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", "replace")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out = {}
    i = 0
    while i + 2 < len(lines) + 1 and i + 2 <= len(lines):
        if lines[i + 1].startswith("1 ") and lines[i + 2].startswith("2 "):
            name = lines[i]
            out[name] = (lines[i + 1], lines[i + 2])
            i += 3
        else:
            i += 1
    return out


def fetch_satnogs_db(timeout: int = 30) -> dict:
    """Download the SatNOGS transponder database.
    Returns {sat_name: [{descr, up, up_mode, down, down_mode, tone, norad}]},
    filtered to transceivers/transponders usable with the IC-9700 SUB band
    (downlink 100-500 MHz), modes normalized to LSB/USB/FM/CW."""
    import json
    import re
    import urllib.request

    def _get(url):
        req = urllib.request.Request(url, headers={"User-Agent": "ic9700-civ-ctrl/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    sats = _get("https://db.satnogs.org/api/satellites/?format=json")
    names = {s["norad_cat_id"]: s["name"] for s in sats if "norad_cat_id" in s}
    trs = _get("https://db.satnogs.org/api/transmitters/?format=json")

    def _mode(m):
        m = (m or "").upper()
        if m in ("FM", "FMN", "WFM"):
            return "FM"
        if m in ("USB", "LSB", "CW"):
            return m
        return None

    db = {}
    for t in trs:
        if not t.get("alive"):
            continue
        down = t.get("downlink_low")
        up = t.get("uplink_low")
        if not down or not (SUB_BAND_MIN_HZ <= down <= SUB_BAND_MAX_HZ):
            continue
        norad = t.get("norad_cat_id")
        name = names.get(norad)
        if not name:
            continue
        # transponder center if a range is given
        if t.get("downlink_high") and t["downlink_high"] > down:
            down = (down + t["downlink_high"]) // 2
        if up and t.get("uplink_high") and t["uplink_high"] > up:
            up = (up + t["uplink_high"]) // 2
        descr = t.get("description") or ""
        tone = None
        m = re.search(r"CTCSS\s+(\d+(?:\.\d+)?)\s*Hz", descr, re.I)
        if m:
            tone = float(m.group(1))
        entry = {
            "descr": descr,
            "up": up,
            "up_mode": _mode(t.get("uplink_mode") or t.get("mode")),
            "down": down,
            "down_mode": _mode(t.get("downlink_mode") or t.get("mode")),
            "tone": tone,
            "norad": norad,
        }
        db.setdefault(name, []).append(entry)
    return db


def norad_of(tle_line1: str) -> str:
    """NORAD catalog number from TLE line 1."""
    return tle_line1[2:7].strip() if len(tle_line1) >= 7 else ""


def match_tle_name(tle_names, sat_name: str):
    """Find a TLE entry matching a preset name (fuzzy: ISS, SO-50, etc.)."""
    key = sat_name.upper().replace(" (VOICE)", "").strip()
    for n in tle_names:
        if n.upper() == key:
            return n
    # common aliases
    aliases = {"ISS": ["ISS (ZARYA)", "ZARYA"], "SO-50": ["SAUDISAT 1C (SO-50)", "SAUDISAT 1C"],
               "AO-91": ["AO-91 (RADFXSAT)", "RADFXSAT (FOX-1B)"],
               "AO-85": ["AO-85 (FOX-1A)", "FOX-1A (AO-85)"],
               "FO-29": ["FO-29 (JAS-2)", "JAS-2 (FO-29)"],
               "AO-7": ["AO-7", "OSCAR 7 (AO-7)"],
               "RS-44": ["RS-44", "DOSAAF-85 (RS-44)"]}
    for cand in aliases.get(key, []):
        for n in tle_names:
            if n.upper() == cand.upper():
                return n
    # substring fallback
    for n in tle_names:
        if key in n.upper():
            return n
    return None


class SatTracker:
    """Background Doppler tracking engine.

    get_controller: callable returning the app's CIVController
    broadcast:      callable(dict) pushing JSON to all WebSocket clients
    """

    def __init__(self, get_controller, broadcast):
        self._get_controller = get_controller
        self._broadcast = broadcast

        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._freq_evt = threading.Event()
        self._freq_report = None  # last 0x03 frequency response (Hz)

        # TLE / observer
        self.tle = {}            # name -> (l1, l2)
        self.tle_time = None     # epoch of last fetch
        self.transp_db = {}      # SatNOGS transponder DB: name -> [entries]
        self.transp_time = None
        self.observer = {"lat": 31.23, "lon": 121.47, "alt_m": 50}

        # Active transponder config
        self.cfg = None          # dict(name, up, up_mode, tone, down, down_mode, invert)
        self.tle_name = None     # actual TLE entry name used

        # Runtime state
        self.running = False
        self.enabled = True      # radio updates enabled (S.A.T. "ENABLE" button)
        self.lock_vfo = True     # mirror user downlink tuning to uplink
        self.fm_step_hz = 1        # FM quantization step (UI default: 1 Hz)
        self.up_off = 0          # user offsets (Hz)
        self.down_off = 0
        self.last_up = None      # last freqs actually sent
        self.last_down = None
        self.status = {}         # last computed status (for late-joining UI)
        self.error = None
        self.restore_on_stop = True   # restore radio state on stop
        self.auto_tle = False         # auto-update TLE at app startup
        self.tle_url = None           # custom TLE URL (None = CelesTrak amateur)
        self.rotator = {"on": False, "host": "127.0.0.1", "port": 12000}
        self._saved = None            # saved radio state

        # favorites & schedule (auto-tracking within favorites)
        self.favs = []                # [{name, up, up_mode, tone, down, down_mode, invert, norad}]
        self.schedule_on = False
        self._sched_thread = None
        self._auto = False            # current tracking was started by scheduler
        self._auto_los = None         # LOS epoch of scheduler-started pass
        self._sched_skip = None       # (name, los_ts) manually stopped auto pass

        # generic response waiter (fed by app.py via feed_response)
        self._resp_evt = threading.Event()
        self._resp_payload = None
        self._wait_key = None    # (cmd, subcmd_prefix_bytes)

    # -- wiring -------------------------------------------------------------
    def feed_freq(self, freq_hz: int):
        """Called by app.py for every CI-V 0x03 (read frequency) response."""
        self._freq_report = freq_hz
        self._freq_evt.set()

    def feed_response(self, cmd: int, payload: bytes):
        """Called by app.py for every CI-V response frame."""
        if cmd == 0x03:
            from civ import bcd_to_freq
            self.feed_freq(bcd_to_freq(payload))
        key = self._wait_key
        if key and cmd == key[0] and payload[:len(key[1])] == key[1]:
            self._resp_payload = bytes(payload)
            self._resp_evt.set()

    def _query(self, send_fn, cmd: int, sub: bytes = b"", timeout: float = 0.5):
        """Send a read command and wait for its matching response payload."""
        self._resp_evt.clear()
        self._resp_payload = None
        self._wait_key = (cmd, sub)
        try:
            send_fn()
            if self._resp_evt.wait(timeout):
                return self._resp_payload
            return None
        finally:
            self._wait_key = None

    def _read_selected_freq(self, timeout=0.4):
        self._freq_evt.clear()
        c = self._get_controller()
        c.read_frequency()
        if self._freq_evt.wait(timeout):
            return self._freq_report
        return None

    # -- configuration ------------------------------------------------------
    def set_observer(self, lat, lon, alt_m=50):
        with self._lock:
            self.observer = {"lat": float(lat), "lon": float(lon), "alt_m": float(alt_m)}

    def set_tle(self, name, l1, l2):
        with self._lock:
            self.tle[name] = (l1.strip(), l2.strip())
        self.save_tle_cache()

    def set_tle_bulk(self, mapping):
        with self._lock:
            self.tle.update(mapping)
            self.tle_time = time.time()
        self.save_tle_cache()

    # -- TLE persistence ------------------------------------------------------
    def save_tle_cache(self):
        import json
        try:
            with self._lock:
                data = {"time": self.tle_time or time.time(),
                        "tle": {k: list(v) for k, v in self.tle.items()}}
            with open(_data_path("tle_cache.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            logger.error("TLE cache save failed: %s", e)

    def load_tle_cache(self):
        """Load TLE from local cache (called at app startup)."""
        import json
        import os
        try:
            path = _data_path("tle_cache.json")
            if not os.path.exists(path):
                return 0
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self.tle.update({k: tuple(v) for k, v in data.get("tle", {}).items()})
                self.tle_time = data.get("time")
            logger.info("TLE cache loaded: %d sats", len(self.tle))
            return len(self.tle)
        except Exception as e:
            logger.error("TLE cache load failed: %s", e)
            return 0

    # -- transponder DB persistence -------------------------------------------
    def set_transp_db(self, db):
        with self._lock:
            self.transp_db = db
            self.transp_time = time.time()
        self.save_transp_cache()

    def save_transp_cache(self):
        import json
        try:
            with self._lock:
                data = {"time": self.transp_time or time.time(), "db": self.transp_db}
            with open(_data_path("transp_cache.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            logger.error("transp cache save failed: %s", e)

    def load_transp_cache(self):
        import json
        import os
        try:
            path = _data_path("transp_cache.json")
            if not os.path.exists(path):
                return 0
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self.transp_db = data.get("db", {})
                self.transp_time = data.get("time")
            logger.info("transponder DB cache loaded: %d sats", len(self.transp_db))
            return len(self.transp_db)
        except Exception as e:
            logger.error("transp cache load failed: %s", e)
            return 0

    def configure(self, name, up, up_mode, tone, down, down_mode, invert,
                  fm_step_hz=None, swap=False, norad=None):
        """Select satellite + transponder. Frequencies in Hz.
        swap=False: MAIN=uplink(TX), SUB=downlink(RX)   — full-duplex TX layout
        swap=True : MAIN=downlink(RX), SUB=uplink      — listen layout
                    (wfview audio comes from MAIN; do NOT transmit!)"""
        sub_side = int(up) if swap else int(down)
        if not (SUB_BAND_MIN_HZ <= sub_side <= SUB_BAND_MAX_HZ):
            raise ValueError(
                f"SUB 侧频率 {sub_side/1e6:.3f} MHz 超出 IC-9700 SUB 波段范围 "
                f"(144/430 MHz，SUB 不支持 1.2 GHz)")
        if band_of(int(up)) and band_of(int(up)) == band_of(int(down)):
            raise ValueError("上行和下行在同一波段，IC-9700 不允许 MAIN/SUB 同波段")
        with self._lock:
            self.cfg = dict(name=name, up=int(up), up_mode=up_mode.upper(),
                            tone=tone, down=int(down), down_mode=down_mode.upper(),
                            invert=bool(invert), swap=bool(swap))
            if fm_step_hz:
                self.fm_step_hz = int(fm_step_hz)
            self.up_off = 0
            self.down_off = 0
            self.tle_name = self._resolve_tle_name(name, norad)

    # -- band assignment helpers ---------------------------------------------
    def _main_target(self):
        """Frequency that should end up on MAIN per current config."""
        return self.cfg["up"] if not self.cfg.get("swap") else self.cfg["down"]

    def _sub_target(self):
        return self.cfg["down"] if not self.cfg.get("swap") else self.cfg["up"]

    def _read_both_bands(self):
        """Read (main_freq, sub_freq) via band selects; leaves MAIN selected."""
        c = self._get_controller()
        c.select_main()
        main = self._read_selected_freq()
        c.select_sub()
        sub = self._read_selected_freq()
        c.select_main()
        return main, sub

    def _stage_bands(self, target_main, target_sub):
        """Bring MAIN/SUB onto the bands of the given target frequencies.

        The radio NG's any 0x05 write that would put MAIN and SUB on the same
        band, so band changes must be staged: exchange when the two bands are
        swapped, otherwise write a band center on the mismatched side first
        (always conflict-free by case analysis)."""
        c = self._get_controller()
        bm, bs = band_of(target_main), band_of(target_sub)
        if not bm or not bs:
            return
        main_f, sub_f = self._read_both_bands()
        cm, cs = band_of(main_f), band_of(sub_f)
        logger.info("band check: MAIN=%s(%s) SUB=%s(%s) -> need MAIN=%s SUB=%s",
                    main_f, cm, sub_f, cs, bm, bs)
        if cm == bm and cs == bs:
            return
        if cm == bs and cs == bm:
            logger.info("bands swapped -> 07 B0 exchange")
            c.vfo_exchange()                     # 07 B0
            time.sleep(0.15)
            return
        if cm == bm:                             # only SUB on wrong band
            c.select_sub()
            c.set_frequency(band_center(bs))     # MAIN stays bm != bs: no conflict
            time.sleep(0.10)
            c.select_main()
            return
        if cs == bs:                             # only MAIN on wrong band
            c.select_main()
            c.set_frequency(band_center(bm))
            time.sleep(0.10)
            return
        # both wrong and not swapped (exotic): move MAIN first (cs != bm here,
        # otherwise it would be the swapped case), then SUB
        c.select_main()
        c.set_frequency(band_center(bm))
        time.sleep(0.10)
        c.select_sub()
        c.set_frequency(band_center(bs))
        time.sleep(0.10)
        c.select_main()

    def _ensure_bands(self):
        self._stage_bands(self._main_target(), self._sub_target())

    # -- radio state save / restore -------------------------------------------
    def _save_radio(self):
        """Snapshot every radio setting the tracker touches, for restore."""
        c = self._get_controller()
        sv = {}
        p = self._query(lambda: c.read_function(0x5A), 0x16, b"\x5A")
        sv["sat_mode"] = p[1] if p and len(p) > 1 else None
        p = self._query(lambda: c.read_function(0x59), 0x16, b"\x59")
        sv["dualwatch"] = p[1] if p and len(p) > 1 else None
        for item in (0x0033, 0x0034, 0x0035):
            p = self._query(lambda i=item: c.read_1a_05(i), 0x1A,
                            bytes([0x05, (item >> 8) & 0xFF, item & 0xFF]))
            sv[f"mute_{item:04x}"] = p[3] if p and len(p) > 3 else None
        p = self._query(lambda: c.read_function(0x42), 0x16, b"\x42")
        sv["rpt_tone"] = p[1] if p and len(p) > 1 else None
        p = self._query(lambda: c.ser.send(0x1B, data=bytes([0x00])), 0x1B, b"\x00")
        sv["tone_bcd"] = p[1:4] if p and len(p) >= 4 else None
        p = self._query(lambda: c.ser.send(0x21, data=bytes([0x01])), 0x21, b"\x01")
        sv["rit_on"] = p[1] if p and len(p) > 1 else None
        p = self._query(lambda: c.ser.send(0x21, data=bytes([0x00])), 0x21, b"\x00")
        sv["rit_bcd"] = p[1:4] if p and len(p) >= 4 else None
        # band freqs + modes
        c.select_main()
        sv["main_freq"] = self._read_selected_freq()
        p = self._query(lambda: c.read_mode(), 0x04)
        sv["main_mode"] = p[:2] if p and len(p) >= 2 else None
        if sv["dualwatch"]:
            c.select_sub()
            sv["sub_freq"] = self._read_selected_freq()
            p = self._query(lambda: c.read_mode(), 0x04)
            sv["sub_mode"] = p[:2] if p and len(p) >= 2 else None
        else:
            sv["sub_freq"] = None
            sv["sub_mode"] = None
        c.select_main()
        self._saved = sv
        logger.info("radio state saved: %s", {k: (v.hex() if isinstance(v, bytes) else v)
                                              for k, v in sv.items()})

    def _restore_radio(self):
        """Restore the radio to the state saved at tracking start."""
        sv = self._saved
        if not sv:
            return
        c = self._get_controller()
        logger.info("restoring radio state...")
        try:
            # 1) freqs/modes first (while dualwatch is still on), staged to
            #    avoid the same-band NG rule
            if sv.get("main_freq") and sv.get("sub_freq"):
                self._stage_bands(sv["main_freq"], sv["sub_freq"])
            if sv.get("main_freq"):
                c.select_main()
                c.set_frequency(sv["main_freq"])
                time.sleep(0.05)
                if sv.get("main_mode"):
                    c.set_mode(sv["main_mode"][0], sv["main_mode"][1])
                    time.sleep(0.05)
            if sv.get("sub_freq"):
                c.select_sub()
                c.set_frequency(sv["sub_freq"])
                time.sleep(0.05)
                if sv.get("sub_mode"):
                    c.set_mode(sv["sub_mode"][0], sv["sub_mode"][1])
                    time.sleep(0.05)
            c.select_main()
            # 2) tone
            if sv.get("tone_bcd"):
                c.ser.send(0x1B, data=bytes([0x00]) + sv["tone_bcd"])
                time.sleep(0.05)
            if sv.get("rpt_tone") is not None:
                c.set_repeater_tone(sv["rpt_tone"])
                time.sleep(0.05)
            # 3) RIT
            if sv.get("rit_bcd"):
                c.ser.send(0x21, 0x00, sv["rit_bcd"])
                time.sleep(0.05)
            if sv.get("rit_on") is not None:
                c.set_rit(bool(sv["rit_on"]))
                time.sleep(0.05)
            # 4) mutes
            for item in (0x0033, 0x0034, 0x0035):
                v = sv.get(f"mute_{item:04x}")
                if v is not None:
                    c.set_1a_05(item, bytes([v]))
                    time.sleep(0.05)
            # 5) dualwatch / satellite mode last
            if sv.get("dualwatch") is not None:
                c.set_sub_band(sv["dualwatch"])
                time.sleep(0.05)
            if sv.get("sat_mode") is not None:
                c.set_satellite_mode(sv["sat_mode"])
                time.sleep(0.05)
            c.select_main()
            logger.info("radio state restored")
        except Exception as e:
            logger.error("restore failed: %s", e)

    def _verify_freqs(self, up, down):
        """Read back both bands and surface a visible error on mismatch."""
        main_f, sub_f = self._read_both_bands()
        swap = self.cfg.get("swap")
        want_main, want_sub = (down, up) if swap else (up, down)
        TOL = 5000
        problems = []
        if main_f is None or abs(main_f - want_main) > TOL:
            problems.append(f"MAIN 读出 {main_f} ≠ 目标 {want_main}")
        if sub_f is None or abs(sub_f - want_sub) > TOL:
            problems.append(f"SUB 读出 {sub_f} ≠ 目标 {want_sub}")
        if problems:
            self.error = "频率校验失败: " + "; ".join(problems)
            logger.error(self.error)
        else:
            if self.error and self.error.startswith("频率校验失败"):
                self.error = None
            logger.info("freq verify OK: MAIN=%s SUB=%s", main_f, sub_f)

    # -- radio session ------------------------------------------------------
    def _setup_radio(self):
        """Put the radio into dual-VFO satellite operating state."""
        c = self._get_controller()
        c.set_satellite_mode(0)                      # 16 5A 00  sat mode OFF
        time.sleep(0.05)
        c.set_sub_band(1)                            # 16 59 01  dualwatch ON
        time.sleep(0.05)
        # Sub Band Mute (TX) = OFF on all outputs, so the downlink stays
        # audible while transmitting (full-duplex self monitoring):
        #   0033 = Speaker/Phones, 0034 = USB, 0035 = LAN
        # 0035 (LAN) is essential for wfview stereo (2ch) audio during TX.
        for item in (0x0033, 0x0034, 0x0035):
            c.set_1a_05(item, bytes([0x00]))
            time.sleep(0.05)

        cfg = self.cfg
        swap = cfg.get("swap")
        main_mode = cfg["down_mode"] if swap else cfg["up_mode"]
        sub_mode = cfg["up_mode"] if swap else cfg["down_mode"]
        # MAIN band mode
        c.select_main()
        time.sleep(0.05)
        c.set_mode(MODE_CODES.get(main_mode, 0x01))
        time.sleep(0.05)
        if cfg.get("tone") and not swap:
            # uplink CTCSS only meaningful on the TX band (MAIN in normal layout)
            c.ser.send(0x1B, data=bytes([0x00]) + tone_to_bcd(cfg["tone"]))
            time.sleep(0.05)
            c.set_repeater_tone(1)                   # 16 42 01
        else:
            c.set_repeater_tone(0)
        time.sleep(0.05)
        # SUB band mode
        c.select_sub()
        time.sleep(0.05)
        c.set_mode(MODE_CODES.get(sub_mode, 0x01))
        time.sleep(0.05)
        c.select_main()                              # leave MAIN selected
        time.sleep(0.05)

    # -- doppler ------------------------------------------------------------
    def _calc(self):
        """Compute current geometry + corrected freqs. Returns dict or None."""
        from skyfield.api import EarthSatellite, wgs84
        with self._lock:
            if not self.cfg or not self.tle_name or self.tle_name not in self.tle:
                return None
            l1, l2 = self.tle[self.tle_name]
            obs = dict(self.observer)
            cfg = dict(self.cfg)
            up_off, down_off = self.up_off, self.down_off

        ts = _timescale()
        sat = EarthSatellite(l1, l2, self.tle_name, ts)
        t = ts.now()
        obsf = wgs84.latlon(obs["lat"], obs["lon"], obs["alt_m"])
        topo = (sat - obsf).at(t)
        # skyfield >= 1.49: range rate via frame_latlon_and_rates
        el_ang, az_ang, dist, _elr, _azr, rr = topo.frame_latlon_and_rates(obsf)
        v = rr.km_per_s * 1000.0  # m/s, + = receding

        down = cfg["down"] * (C - v) / C + down_off
        up = cfg["up"] * C / (C - v) + up_off

        fm = cfg["down_mode"] == "FM" and cfg["up_mode"] == "FM"
        if fm:
            step = self.fm_step_hz
            down = round(down / step) * step
            up = round(up / step) * step
        else:
            down = round(down / 10) * 10
            up = round(up / 10) * 10

        return dict(name=cfg["name"], tle_name=self.tle_name,
                    up=int(up), down=int(down),
                    up_shift=int(up - cfg["up"]), down_shift=int(down - cfg["down"]),
                    up_off=int(up_off), down_off=int(down_off),
                    range_rate=float(round(v, 1)), az=float(round(az_ang.degrees, 1)),
                    el=float(round(el_ang.degrees, 1)), range_km=float(round(dist.km, 0)),
                    above=bool(el_ang.degrees > 0), fm=fm)

    def predict_pass(self, horizon=0.0):
        """Next AOS/LOS for the configured satellite (blocking, ~seconds)."""
        from skyfield.api import EarthSatellite, wgs84
        with self._lock:
            if not self.cfg or not self.tle_name or self.tle_name not in self.tle:
                return None
            l1, l2 = self.tle[self.tle_name]
            obs = dict(self.observer)
        ts = _timescale()
        sat = EarthSatellite(l1, l2, self.tle_name, ts)
        obsf = wgs84.latlon(obs["lat"], obs["lon"], obs["alt_m"])
        t0 = ts.now()
        t1 = ts.utc(t0.utc[0], t0.utc[1], t0.utc[2] + 1)  # +24 h
        times, events = sat.find_events(obsf, t0, t1, altitude_degrees=horizon)
        aos = los = maxel = None
        for ti, ev in zip(times, events):
            if ev == 0 and aos is None:
                aos = ti.utc_strftime("%H:%M:%S")
            elif ev == 1 and aos is not None and maxel is None:
                maxel = float(round((sat - obsf).at(ti).altaz()[0].degrees, 1))
            elif ev == 2 and aos is not None:
                los = ti.utc_strftime("%H:%M:%S")
                break
        return dict(aos=aos, los=los, max_el=maxel)

    def _resolve_tle_name(self, name, norad=None):
        """Resolve a satellite name to a TLE entry name (name fuzzy, then NORAD)."""
        tn = match_tle_name(self.tle.keys(), name)
        if not tn and norad:
            for n, (l1, _l2) in self.tle.items():
                if norad_of(l1) == str(norad):
                    tn = n
                    break
        return tn

    def _find_passes(self, hours: float = 48.0, cfg: dict = None, tle_name: str = None):
        """All AOS/culmination/LOS events within `hours`. Returns list of
        (t_aos, t_cul_or_None, t_los_or_None) skyfield times.
        cfg/tle_name default to the currently configured satellite."""
        from skyfield.api import EarthSatellite, wgs84
        with self._lock:
            cfg = cfg or self.cfg
            tle_name = tle_name or self.tle_name
            if not cfg or not tle_name or tle_name not in self.tle:
                return None, None, None
            l1, l2 = self.tle[tle_name]
            obs = dict(self.observer)
        ts = _timescale()
        sat = EarthSatellite(l1, l2, self.tle_name, ts)
        obsf = wgs84.latlon(obs["lat"], obs["lon"], obs["alt_m"])
        t0 = ts.now()
        t1 = ts.tt_jd(t0.tt + hours / 24.0)
        times, events = sat.find_events(obsf, t0, t1, altitude_degrees=0.0)
        passes = []
        t_aos = t_cul = None
        for ti, ev in zip(times, events):
            if ev == 0:
                t_aos, t_cul = ti, None
            elif ev == 1 and t_aos is not None:
                t_cul = ti
            elif ev == 2 and t_aos is not None:
                passes.append((t_aos, t_cul, ti))
                t_aos = t_cul = None
        return sat, obsf, passes

    def predict_passes(self, hours: float = 48.0):
        """List of passes within `hours`: [{aos, los, max_el, dur_min}]."""
        sat, obsf, passes = self._find_passes(hours)
        if passes is None:
            return None
        out = []
        for t_aos, t_cul, t_los in passes:
            if t_cul is not None:
                el = float((sat - obsf).at(t_cul).altaz()[0].degrees)
            else:
                el = float(round(max(
                    (sat - obsf).at(t_aos).altaz()[0].degrees,
                    (sat - obsf).at(t_los).altaz()[0].degrees), 1))
            out.append({
                "aos": t_aos.utc_strftime("%m-%d %H:%M:%S"),
                "los": t_los.utc_strftime("%H:%M:%S"),
                "aos_ts": t_aos.utc_datetime().timestamp(),
                "los_ts": t_los.utc_datetime().timestamp(),
                "max_el": float(round(el, 1)),
                "dur_min": float(round((t_los.tt - t_aos.tt) * 1440, 1)),
            })
        return out

    def predict_pass_profile(self, hours: float = 48.0, index: int = 0, step_s: int = 30):
        """Elevation/azimuth profile of the index-th pass, for plotting.
        Returns {aos, los, max_el, step_s, points:[{t, el, az}]} or None."""
        sat, obsf, passes = self._find_passes(hours)
        if not passes or index >= len(passes):
            return None
        t_aos, _tc, t_los = passes[index]
        ts = _timescale()
        points = []
        max_el = -90.0
        n = int((t_los.tt - t_aos.tt) * 86400 / step_s) + 1
        for i in range(n + 1):
            tt = t_aos.tt + i * step_s / 86400.0
            ti = ts.tt_jd(tt)
            el_ang, az_ang, _d, _er, _ar, _rr = (sat - obsf).at(ti).frame_latlon_and_rates(obsf)
            el = float(round(el_ang.degrees, 1))
            max_el = max(max_el, el)
            points.append({"t": ti.utc_strftime("%H:%M"),
                           "ts": ti.utc_datetime().timestamp(),
                           "el": el, "az": float(round(az_ang.degrees, 1))})
        return {"aos": t_aos.utc_strftime("%m-%d %H:%M:%S"),
                "los": t_los.utc_strftime("%H:%M:%S"),
                "aos_ts": t_aos.utc_datetime().timestamp(),
                "los_ts": t_los.utc_datetime().timestamp(),
                "max_el": float(round(max_el, 1)),
                "index": index,
                "step_s": step_s, "points": points}

    # -- CI-V update --------------------------------------------------------
    def _push_freqs(self, up, down):
        """Send changed freqs to the radio, minimal band-select thrash.
        Role->band mapping depends on cfg['swap']."""
        c = self._get_controller()
        swap = self.cfg.get("swap")
        send_up = up != self.last_up
        send_down = down != self.last_down
        # uplink goes to MAIN in normal layout, to SUB when swapped
        if send_up:
            c.select_sub() if swap else c.select_main()
            c.set_frequency(up)
            self.last_up = up
        if send_down:
            c.select_main() if swap else c.select_sub()
            c.set_frequency(down)
            self.last_down = down
        if send_up or send_down:
            c.select_main()  # keep MAIN selected (wfview focus band)
            time.sleep(0.02)

    def _lock_poll(self):
        """Read the downlink band freq; mirror user tuning into offsets."""
        if not self.lock_vfo or self.last_down is None:
            return
        c = self._get_controller()
        if self.cfg.get("swap"):
            actual = self._read_selected_freq()  # downlink is MAIN, already selected
        else:
            c.select_sub()
            actual = self._read_selected_freq()
            c.select_main()
        if not actual:
            return
        delta = actual - self.last_down
        if delta == 0 or abs(delta) > 2_000_000:
            return  # no user tuning, or implausible jump (ignore)
        with self._lock:
            self.down_off += delta
            self.up_off += -delta if self.cfg["invert"] else delta
        self.last_down = actual
        logger.info("LOCK VFO: user tuned downlink %+d Hz -> offsets down=%+d up=%+d",
                    delta, self.down_off, self.up_off)

    # -- PstRotator (UDP control, port 12000 default) --------------------------
    def set_rotator(self, on=None, host=None, port=None):
        with self._lock:
            if on is not None:
                self.rotator["on"] = bool(on)
            if host:
                self.rotator["host"] = host.strip()
            if port:
                self.rotator["port"] = int(port)

    def _rotator_update(self, az: float, el: float):
        """Send AZ/EL to PstRotator via its official UDP control protocol.
        (PstRotator: Setup -> Communication -> UDP Control Port, default 12000;
        antenna position reports come back on port+1.)"""
        import socket
        el = max(0.0, min(90.0, el))
        az = az % 360.0
        msg = (f"<PST><TRACK>0</TRACK><AZIMUTH>{az:.1f}</AZIMUTH>"
               f"<ELEVATION>{el:.1f}</ELEVATION></PST>")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.sendto(msg.encode("ascii"),
                         (self.rotator["host"], self.rotator["port"]))
        except Exception as e:
            logger.error("rotator udp error: %s", e)
    def start(self):
        if self.running:
            return
        if not self.cfg:
            raise RuntimeError("未配置卫星/转发器")
        if not self.tle_name:
            raise RuntimeError(f"找不到 {self.cfg['name']} 的 TLE，请先更新 TLE")
        self._stop.clear()
        self.running = True
        self.enabled = True
        self.last_up = None
        self.last_down = None
        self.error = None
        self._auto = False           # manual start; scheduler sets True after
        self._auto_los = None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        self.running = False
        # manual stop of a scheduler-started pass: skip it until its LOS so the
        # scheduler does not immediately restart it
        if self._auto and self._auto_los and self.cfg:
            self._sched_skip = (self.cfg["name"], self._auto_los)
        self._auto = False
        self._auto_los = None
        if self.restore_on_stop and self._saved:
            try:
                self._restore_radio()
            except Exception as e:
                logger.error("restore on stop failed: %s", e)
        self._saved = None
        self._broadcast_status()  # final state

    def _loop(self):
        try:
            self._save_radio()         # snapshot for restore_on_stop
            self._setup_radio()
            st0 = self._calc()
            if st0:
                self._ensure_bands()   # 0x05 is NG'd if it would create a
            self.last_up = None        # same-band MAIN/SUB pair -> stage first
            self.last_down = None
        except Exception as e:
            self.error = f"电台初始化失败: {e}"
            logger.error(self.error)
        interval = 1.0 if (self.cfg and self.cfg["up_mode"] == "FM"
                           and self.cfg["down_mode"] == "FM") else 0.5
        last_lock = 0.0
        last_bcast = 0.0
        last_rot = 0.0
        verified = False
        while not self._stop.is_set():
            try:
                if self.enabled:
                    st = self._calc()
                    if st:
                        self.status = st
                        self._push_freqs(st["up"], st["down"])
                        if self.rotator["on"] and time.monotonic() - last_rot > 1.0:
                            self._rotator_update(st["az"], st["el"])
                            last_rot = time.monotonic()
                        if not verified and self.last_up is not None \
                                and self.last_down is not None:
                            self._verify_freqs(st["up"], st["down"])
                            verified = True
                        if time.monotonic() - last_lock > 1.5:
                            self._lock_poll()
                            last_lock = time.monotonic()
                if time.monotonic() - last_bcast > 0.5:
                    self._broadcast_status()
                    last_bcast = time.monotonic()
            except Exception as e:
                logger.error("sat loop error: %s", e)
            self._stop.wait(interval)
        try:
            self._get_controller().select_main()
        except Exception:
            pass

    # -- user actions -------------------------------------------------------
    def nudge(self, which, delta_hz):
        with self._lock:
            if which == "up":
                self.up_off += int(delta_hz)
            else:
                self.down_off += int(delta_hz)
        self._broadcast_status()

    def center(self):
        with self._lock:
            self.up_off = 0
            self.down_off = 0
        self.last_down = None  # avoid LOCK false-trigger on next poll
        self._broadcast_status()

    # -- favorites ------------------------------------------------------------
    def fav_add(self, cfg: dict):
        """Add/update a favorite. cfg: {name, up, up_mode, tone, down,
        down_mode, invert, norad}."""
        fav = {k: cfg.get(k) for k in
               ("name", "up", "up_mode", "tone", "down", "down_mode", "invert", "norad")}
        with self._lock:
            self.favs = [f for f in self.favs if f["name"] != fav["name"]]
            self.favs.append(fav)
            self.favs.sort(key=lambda f: f["name"].lower())
        self._broadcast_status()

    def fav_del(self, name: str):
        with self._lock:
            self.favs = [f for f in self.favs if f["name"] != name]
        self._broadcast_status()

    def fav_pass_table(self, hours: float = 24.0, min_el: float = 0.0):
        """Next pass for each favorite, sorted by AOS (S.A.T. NEXT PASSES).
        Rows: {name, aos, los, aos_ts, los_ts, max_el, dur_min, cfg, no_tle}."""
        rows = []
        with self._lock:
            favs = list(self.favs)
        for fav in favs:
            tn = self._resolve_tle_name(fav["name"], fav.get("norad"))
            if not tn:
                rows.append({"name": fav["name"], "no_tle": True, "cfg": fav})
                continue
            _sat, _obsf, passes = self._find_passes(hours, cfg=fav, tle_name=tn)
            if not passes:
                continue
            t_aos, t_cul, t_los = passes[0]
            # max el: use culmination event when available, else midpoint sample
            if t_cul is not None:
                el_ang = self._el_at(tn, t_cul)
            else:
                ts = _timescale()
                el_ang = self._el_at(tn, ts.tt_jd((t_aos.tt + t_los.tt) / 2))
            max_el = float(round(el_ang, 1)) if el_ang is not None else 0.0
            if max_el < min_el:
                continue
            rows.append({
                "name": fav["name"],
                "aos": t_aos.utc_strftime("%m-%d %H:%M:%S"),
                "los": t_los.utc_strftime("%H:%M:%S"),
                "aos_ts": t_aos.utc_datetime().timestamp(),
                "los_ts": t_los.utc_datetime().timestamp(),
                "max_el": max_el,
                "dur_min": float(round((t_los.tt - t_aos.tt) * 1440, 1)),
                "cfg": fav,
            })
        rows.sort(key=lambda r: r.get("aos_ts", 9e18))
        return rows

    def _el_at(self, tle_name, ti):
        """Elevation (degrees) of satellite `tle_name` at skyfield time `ti`."""
        from skyfield.api import EarthSatellite, wgs84
        with self._lock:
            if tle_name not in self.tle:
                return None
            l1, l2 = self.tle[tle_name]
            obs = dict(self.observer)
        ts = _timescale()
        sat = EarthSatellite(l1, l2, tle_name, ts)
        obsf = wgs84.latlon(obs["lat"], obs["lon"], obs["alt_m"])
        return float((sat - obsf).at(ti).altaz()[0].degrees)

    # -- scheduler (auto-tracking within favorites) ----------------------------
    def set_schedule(self, on: bool):
        on = bool(on)
        if on and not self.schedule_on:
            self.schedule_on = True
            self._sched_skip = None
            self._sched_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
            self._sched_thread.start()
            logger.info("schedule ON (%d favorites)", len(self.favs))
        elif not on and self.schedule_on:
            self.schedule_on = False
            logger.info("schedule OFF")
        self._broadcast_status()

    def _sched_start(self, row):
        """Scheduler-driven tracking start (S.A.T.: ~60 s before AOS)."""
        fav = row["cfg"]
        try:
            self.configure(fav["name"], fav["up"], fav.get("up_mode") or "USB",
                           fav.get("tone"), fav["down"], fav.get("down_mode") or "USB",
                           fav.get("invert", False), norad=fav.get("norad"))
            self.start()
            self._auto = True             # set AFTER start() (start resets it)
            self._auto_los = row["los_ts"]
            logger.info("schedule: auto-tracking %s (LOS %s)", fav["name"], row["los"])
        except Exception as e:
            logger.error("schedule start failed for %s: %s", fav["name"], e)
            self._auto = False

    def _scheduler_loop(self):
        """S.A.T. SCHEDULE semantics:
        - track favorites in AOS order, starting ~60 s before AOS
        - never interrupt manual tracking
        - overlapping passes: earliest AOS wins, tracked to LOS
        - manual stop of an auto pass: skip it until its LOS"""
        while self.schedule_on:
            try:
                now = time.time()
                if self._auto and self.running and self._auto_los and now > self._auto_los:
                    logger.info("schedule: LOS reached, stopping auto tracking")
                    self._auto = False
                    self.stop()
                if self.schedule_on:
                    rows = self.fav_pass_table(hours=12, min_el=-90)
                    for row in rows:
                        if row.get("no_tle"):
                            continue
                        aos, los = row["aos_ts"], row["los_ts"]
                        if los < now:
                            continue
                        if self._sched_skip and row["name"] == self._sched_skip[0] \
                                and now < self._sched_skip[1]:
                            continue  # manually stopped this auto pass
                        if aos - 60 <= now < los:
                            if not self.running:
                                self._sched_start(row)
                            elif self._auto and self.cfg \
                                    and self.cfg["name"] != row["name"] \
                                    and self._auto_los and now > self._auto_los:
                                self._sched_start(row)
                            break  # earliest-AOS active pass decided
            except Exception as e:
                logger.error("scheduler error: %s", e)
            for _ in range(30):
                if not self.schedule_on:
                    break
                time.sleep(1)

    # -- status -------------------------------------------------------------
    def _broadcast_status(self):
        self._broadcast(self.get_state())

    def get_state(self):
        with self._lock:
            cfg = dict(self.cfg) if self.cfg else None
            obs = dict(self.observer)
            up_off, down_off = self.up_off, self.down_off
        st = dict(self.status)
        return {
            "type": "sat_status",
            "running": self.running,
            "enabled": self.enabled,
            "lock_vfo": self.lock_vfo,
            "restore_on_stop": self.restore_on_stop,
            "auto_tle": self.auto_tle,
            "tle_url": self.tle_url,
            "rotator": dict(self.rotator),
            "favs": self.favs,
            "schedule_on": self.schedule_on,
            "auto": self._auto,
            "tle_max_age_days": TLE_MAX_AGE_DAYS,
            "error": self.error,
            "cfg": cfg,
            "observer": obs,
            "up_off": up_off,
            "down_off": down_off,
            "tle_count": len(self.tle),
            "tle_time": self.tle_time,
            "transp_count": len(self.transp_db),
            "transp_time": self.transp_time,
            "tle_names": sorted(self.tle.keys())[:400],
            "presets": {k: v for k, v in SATELLITE_PRESETS.items()},
            "tones": CTCSS_TONES,
            "calc": st or None,
        }
