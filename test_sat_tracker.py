"""Offline test for sat_tracker: Doppler math + CI-V sequence + band adaptation."""
import threading
import time
import sat_tracker
from sat_tracker import SatTracker, fetch_tle_celestrak, match_tle_name, band_of

# ---------- 1. TLE fetch (live) or fallback ----------
ISS_TLE = ("1 25544U 98067A   25257.50000000  .00010000  00000-0  18000-3 0  9993",
           "2 25544  51.6400 300.0000 0005000  90.0000  70.0000 15.50000000525993")
try:
    tle = fetch_tle_celestrak()
    print(f"[1] CelesTrak fetch OK: {len(tle)} sats")
    name = match_tle_name(tle.keys(), "ISS (VOICE)")
    print(f"    ISS matched TLE name: {name!r}")
except Exception as e:
    print(f"[1] CelesTrak fetch FAILED ({e}); using embedded ISS TLE")
    tle = {"ISS (ZARYA)": ISS_TLE}
    name = "ISS (ZARYA)"

# ---------- 2. Doppler math sanity ----------
from skyfield.api import load, EarthSatellite, wgs84
l1, l2 = tle[name]
ts = load.timescale()
sat = EarthSatellite(l1, l2, name, ts)
obs = wgs84.latlon(31.23, 121.47, 50)
t = ts.now()
topo = (sat - obs).at(t)
_el, _az, _d, _er, _ar, rr = topo.frame_latlon_and_rates(obs)
v = rr.km_per_s * 1000
el = _el.degrees
print(f"[2] ISS now: el={el:.1f} deg, range_rate={v:.0f} m/s")

C = 299792458.0
f_down, f_up = 437_800_000, 145_990_000
down_rx = f_down * (C - v) / C
up_tx = f_up * C / (C - v)
print(f"    downlink: nominal {f_down} -> RX {down_rx:.0f} (shift {down_rx-f_down:+.0f} Hz)")
print(f"    uplink:   nominal {f_up} -> TX {up_tx:.0f} (shift {up_tx-f_up:+.0f} Hz)")
assert abs(down_rx - f_down) < 15000, "UHF doppler out of range"
assert abs(up_tx - f_up) < 6000, "VHF doppler out of range"
if v < -100:
    assert down_rx > f_down and up_tx < f_up, "doppler sign wrong (approaching)"
    print("    sign check (approaching): OK")
elif v > 100:
    assert down_rx < f_down and up_tx > f_up, "doppler sign wrong (receding)"
    print("    sign check (receding): OK")
else:
    print("    (near TCA, sign check skipped)")

# ---------- 3. Fake radio simulating the user's real rig + same-band NG rule ----------
from civ import CIVController, bcd_to_freq, build_command, freq_to_bcd
from sat_tracker import tone_to_bcd

frames = []

class FakeRadio:
    """IC-9700 model: stateful emulation of band/mode/tone/RIT/function items.
    0x05 write that would put MAIN and SUB on the same band is rejected (NG) —
    the exact behavior observed on the user's real rig."""
    def __init__(self):
        # observed on the user's rig: MAIN=437.440519 (70CM), SUB=144.460 (2M)
        self.bands = {"main": 437_440_519, "sub": 144_460_000}
        self.mode = {"main": (0x01, 0x01), "sub": (0x00, 0x01)}  # USB / LSB
        self.sel = "main"
        self.fn = {0x5A: 0, 0x59: 1, 0x42: 0}   # sat off, dualwatch on, tone off
        self.mutes = {0x0033: 1, 0x0034: 1, 0x0035: 1}  # sub mute ON (default)
        self.tone_bcd = tone_to_bcd(88.5)
        self.rit_on = 1
        self.rit_bcd = b"\x00\x05\x00"          # +500 Hz
        self.ng = []
        self.tracker = None

    def snapshot(self):
        return dict(bands=dict(self.bands), mode=dict(self.mode), fn=dict(self.fn),
                    mutes=dict(self.mutes), tone_bcd=self.tone_bcd,
                    rit_on=self.rit_on, rit_bcd=self.rit_bcd)

    def is_open(self): return True
    def send_raw(self, data):
        frames.append(data)
        return True
    def _respond(self, cmd, payload):
        if self.tracker:
            threading.Timer(0.005, lambda: self.tracker.feed_response(cmd, payload)).start()
    def send(self, cmd, subcmd=None, data=None):
        frames.append(build_command(cmd, subcmd, data))
        d = bytes(data or b"")
        full = (bytes([subcmd]) + d) if subcmd is not None else d
        if cmd == 0x07 and d:
            v = d[0]
            if v == 0xD0: self.sel = "main"
            elif v == 0xD1: self.sel = "sub"
            elif v == 0xB0:
                self.bands["main"], self.bands["sub"] = self.bands["sub"], self.bands["main"]
                self.mode["main"], self.mode["sub"] = self.mode["sub"], self.mode["main"]
        elif cmd == 0x05 and d:
            f = bcd_to_freq(d[:5])
            other = "sub" if self.sel == "main" else "main"
            if band_of(f) and band_of(f) == band_of(self.bands[other]):
                self.ng.append((self.sel, f))  # same-band conflict -> NG
            else:
                self.bands[self.sel] = f
        elif cmd == 0x03:
            self._respond(0x03, freq_to_bcd(self.bands[self.sel]))
        elif cmd == 0x04:
            self._respond(0x04, bytes(self.mode[self.sel]))
        elif cmd == 0x06 and d:
            self.mode[self.sel] = (d[0], d[1] if len(d) > 1 else 1)
        elif cmd == 0x16:
            if len(full) == 1:
                self._respond(0x16, bytes([full[0], self.fn.get(full[0], 0)]))
            elif len(full) >= 2:
                self.fn[full[0]] = full[1]
        elif cmd == 0x1A and full[:1] == b"\x05" and len(full) >= 3:
            item = (full[1] << 8) | full[2]
            if len(full) == 3:
                self._respond(0x1A, bytes([0x05, full[1], full[2], self.mutes.get(item, 0)]))
            else:
                self.mutes[item] = full[3]
        elif cmd == 0x1B and full[:1] == b"\x00":
            if len(full) == 1:
                self._respond(0x1B, b"\x00" + self.tone_bcd)
            else:
                self.tone_bcd = full[1:4]
        elif cmd == 0x21:
            if full[:1] == b"\x01":
                if len(full) == 1: self._respond(0x21, bytes([0x01, self.rit_on]))
                else: self.rit_on = full[1]
            elif full[:1] == b"\x00":
                if len(full) == 1: self._respond(0x21, b"\x00" + self.rit_bcd)
                else: self.rit_bcd = full[1:4]
        return True

fake = FakeRadio()
initial = fake.snapshot()
tracker = SatTracker(lambda: CIVController(fake), lambda m: None)
fake.tracker = tracker
tracker.set_tle(name, l1, l2)
tracker.set_observer(31.23, 121.47, 50)
# ISS: bands are swapped vs. the rig's initial state so the 07 B0 exchange
# path is exercised; the rig's initial modes (USB/LSB) differ from the tracked
# FM so mode restore is observable.
tracker.configure("ISS (VOICE)", 145_990_000, "FM", 67.0, 437_800_000, "FM", False)
tracker.lock_vfo = False  # skip lock polling in test
tracker.start()
time.sleep(3.0)
tracker.stop()

print(f"[3] captured {len(frames)} CI-V frames, same-band NG conflicts: {fake.ng}")
print(f"    tracked state was: MAIN=145.99/FM SUB=437.80/FM")

hexs = [f.hex().upper() for f in frames]
def has(cmd_hex): return any(h == cmd_hex for h in hexs)
def starts(prefix): return any(h.startswith(prefix.replace(" ", "")) for h in hexs)

assert has("FEFE A2E0 165A 00FD".replace(" ", "")), "missing sat-mode OFF"
assert has("FEFE A2E0 1659 01FD".replace(" ", "")), "missing dualwatch ON"
assert starts("FE FEA2E01A050033"), "missing sub-mute-OFF 0033"
assert starts("FE FEA2E01A050034"), "missing sub-mute-OFF 0034"
assert starts("FE FEA2E01A050035"), "missing sub-mute-OFF 0035"
assert has("FEFE A2E0 07D0 FD".replace(" ", "")), "missing select MAIN"
assert has("FEFE A2E0 07D1 FD".replace(" ", "")), "missing select SUB"
assert starts("FE FEA2E01B00"), "missing tone set"
assert has("FEFE A2E0 1642 01FD".replace(" ", "")), "missing tone ON"
assert has("FEFE A2E0 1642 00FD".replace(" ", "")), "missing tone OFF (restore)"

# band adaptation: rig started swapped vs targets -> expect 07 B0 exchange
assert has("FEFE A2E0 07B0 FD".replace(" ", "")), "missing band exchange (07 B0)"
# the whole point: after staging, NO 0x05 write may hit the same-band NG rule
assert not fake.ng, f"same-band NG(s) occurred: {fake.ng}"
assert tracker.error is None, f"unexpected verify error: {tracker.error}"

# ---- restore_on_stop: radio must be back to the exact initial state ----
final = fake.snapshot()
assert final == initial, f"radio state NOT restored:\ninitial={initial}\nfinal ={final}"
print("[3] band adaptation + full state restore OK (zero same-band NG)")

# ---------- 3b. swap mode (listen layout: MAIN=downlink) ----------
fake2 = FakeRadio()
tracker_b = SatTracker(lambda: CIVController(fake2), lambda m: None)
fake2.tracker = tracker_b
tracker_b.set_tle(name, l1, l2)
tracker_b.set_observer(31.23, 121.47, 50)
tracker_b.configure("ISS (VOICE)", 145_990_000, "FM", 67.0, 437_800_000, "FM", False, swap=True)
tracker_b.lock_vfo = False
tracker_b.restore_on_stop = False  # keep tracked state for assertions
tracker_b.start()
time.sleep(3.0)
tracker_b.stop()
bm2, bs2 = band_of(fake2.bands["main"]), band_of(fake2.bands["sub"])
print(f"[3b] swap final: MAIN={fake2.bands['main']}({bm2}) SUB={fake2.bands['sub']}({bs2}) NG={fake2.ng}")
assert not fake2.ng
assert bm2 == "70CM" and abs(fake2.bands["main"] - 437_800_000) < 15000
assert bs2 == "2M" and abs(fake2.bands["sub"] - 145_990_000) < 6000
print("[3b] swap mode OK (MAIN=downlink)")

# ---------- 4. predict_pass ----------
tracker2 = SatTracker(lambda: CIVController(fake), lambda m: None)
tracker2.set_tle(name, l1, l2)
tracker2.set_observer(31.23, 121.47, 50)
tracker2.configure("ISS (VOICE)", 145_990_000, "FM", 67.0, 437_800_000, "FM", False)
t0 = time.time()
p = tracker2.predict_pass()
print(f"[4] next pass ({time.time()-t0:.1f}s): {p}")
assert p is not None

# ---------- 5. JSON serializability of broadcast payloads ----------
import json
json.dumps(tracker.get_state())
json.dumps(p)
print("[5] get_state/pass JSON OK")

# ---------- 7. grid_to_latlon ----------
from sat_tracker import grid_to_latlon
lat, lon = grid_to_latlon("PM01PE")
print(f"[7] PM01PE -> lat={lat:.5f} lon={lon:.5f}")
assert abs(lat - 31.18750) < 0.01 and abs(lon - 121.29167) < 0.01
lat4, lon4 = grid_to_latlon("PM01")
assert abs(lat4 - 31.5) < 0.01 and abs(lon4 - 121.0) < 0.01
try:
    grid_to_latlon("ZZ99")
    raise SystemExit("grid validation failed")
except ValueError:
    pass
print("[7] grid_to_latlon OK")

# ---------- 8. TLE cache save/load ----------
tracker.save_tle_cache()
tracker3 = SatTracker(lambda: CIVController(fake), lambda m: None)
n = tracker3.load_tle_cache()
assert n >= 1 and tracker3.tle_time
print(f"[8] TLE cache save/load OK ({n} sats)")

# ---------- 9. pass profile + 48h pass list ----------
prof = tracker2.predict_pass_profile()
assert prof and len(prof["points"]) > 2 and prof["aos"] and prof["los"]
assert max(pt["el"] for pt in prof["points"]) <= 90
json.dumps(prof)
print(f"[9] pass profile OK: {len(prof['points'])} pts, max_el={prof['max_el']}°")
plist = tracker2.predict_passes(48.0)
assert plist and len(plist) >= 1
assert plist[0].get("aos_ts") and plist[0].get("los_ts")
json.dumps(plist)
print(f"[9b] 48h pass list OK: {len(plist)} passes, first max_el={plist[0]['max_el']}°")
prof2 = tracker2.predict_pass_profile(48.0, 1 if len(plist) > 1 else 0)
assert prof2 and prof2["points"] and prof2["points"][0].get("ts")
print(f"[9c] indexed profile OK (index={prof2['index']})")

# ---------- 9d. SatNOGS transponder DB + NORAD matching ----------
from sat_tracker import fetch_satnogs_db, norad_of
try:
    db = fetch_satnogs_db()
    print(f"[9d] SatNOGS DB OK: {len(db)} sats")
    assert len(db) > 50
    flat = [(n, e) for n, es in db.items() for e in es]
    so50 = [(n, e) for n, e in flat if e.get("norad") == 27607 and e.get("up")]
    assert so50, "SO-50 transceiver entry not found"
    n50, e50 = so50[0]
    print(f"    SO-50: {n50} up={e50['up']} down={e50['down']} tone={e50['tone']} ({e50['descr']})")
    assert e50["up"] == 145_850_000 and e50["down"] == 436_795_000
    assert e50["tone"] == 67.0
    # NORAD-based TLE matching: configure with a deliberately wrong name
    tracker5 = SatTracker(lambda: CIVController(fake), lambda m: None)
    tracker5.set_tle(name, l1, l2)  # ISS TLE, norad 25544
    tracker5.set_observer(31.23, 121.47, 50)
    tracker5.configure("某卫星", 145_990_000, "FM", 67.0, 437_800_000, "FM", False,
                       norad=25544)
    assert tracker5.tle_name == name, f"NORAD match failed: {tracker5.tle_name}"
    assert norad_of(l1) == "25544"
    print("[9d] SatNOGS fetch + NORAD->TLE match OK")
except Exception as e:
    print(f"[9d] SatNOGS live fetch skipped/failed (network): {e}")

# ---------- 10. PstRotator UDP protocol ----------
import socket, re
rx = []
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(0.2)
sock.bind(("127.0.0.1", 12999))
def _udp_listener():
    end = time.time() + 4
    while time.time() < end:
        try:
            m, _ = sock.recvfrom(512)
            rx.append(m.decode("ascii"))
        except socket.timeout:
            pass
lt = threading.Thread(target=_udp_listener, daemon=True)
lt.start()
fake3 = FakeRadio()
tracker4 = SatTracker(lambda: CIVController(fake3), lambda m: None)
fake3.tracker = tracker4
tracker4.set_tle(name, l1, l2)
tracker4.set_observer(31.23, 121.47, 50)
tracker4.configure("ISS (VOICE)", 145_990_000, "FM", 67.0, 437_800_000, "FM", False)
tracker4.set_rotator(on=True, host="127.0.0.1", port=12999)
tracker4.lock_vfo = False
tracker4.restore_on_stop = False
tracker4.start()
time.sleep(2.5)
tracker4.stop()
lt.join(timeout=5)
sock.close()
assert rx, "no rotator UDP packet received"
m = rx[0]
assert re.fullmatch(r"<PST><TRACK>0</TRACK><AZIMUTH>\d{1,3}\.\d</AZIMUTH><ELEVATION>\d{1,2}\.\d</ELEVATION></PST>", m), m
print(f"[10] PstRotator UDP OK: {m}")

# ---------- 11. favorites / schedule / fav pass table ----------
tracker6 = SatTracker(lambda: CIVController(fake), lambda m: None)
tracker6.set_tle(name, l1, l2)
tracker6.set_observer(31.23, 121.47, 50)
iss_fav = {"name": "ISS (VOICE)", "up": 145_990_000, "up_mode": "FM", "tone": 67.0,
           "down": 437_800_000, "down_mode": "FM", "invert": False, "norad": 25544}
tracker6.fav_add(iss_fav)
tracker6.fav_add(dict(iss_fav, name="SO-50", up=145_850_000, down=436_795_000, norad=27607))
assert len(tracker6.favs) == 2
tracker6.fav_add(iss_fav)  # re-add same name -> update, not duplicate
assert len(tracker6.favs) == 2
rows = tracker6.fav_pass_table(hours=24, min_el=0)
# ISS has TLE (resolvable via norad), SO-50 has no TLE loaded
iss_rows = [r for r in rows if r["name"] == "ISS (VOICE)"]
so50_rows = [r for r in rows if r["name"] == "SO-50"]
assert iss_rows and iss_rows[0].get("aos_ts") and iss_rows[0]["max_el"] is not None
assert so50_rows and so50_rows[0].get("no_tle")
assert rows == sorted(rows, key=lambda r: r.get("aos_ts", 9e18))
json.dumps(rows)
print(f"[11] favs + fav pass table OK: {[(r['name'], r.get('max_el', 'noTLE')) for r in rows]}")
# scheduler on/off + manual-stop skip logic
tracker6.set_schedule(True)
assert tracker6.schedule_on and tracker6._sched_thread
time.sleep(1.0)
assert not tracker6.running  # ISS next pass is hours away -> no auto start
tracker6.set_schedule(False)
assert not tracker6.schedule_on
# manual stop of an auto pass records skip-until-LOS
tracker6.configure("ISS (VOICE)", 145_990_000, "FM", 67.0, 437_800_000, "FM", False, norad=25544)
tracker6.restore_on_stop = False
tracker6.lock_vfo = False
tracker6.start()
tracker6._auto = True
tracker6._auto_los = time.time() + 600
tracker6.stop()
assert tracker6._sched_skip and tracker6._sched_skip[0] == "ISS (VOICE)"
assert not tracker6._auto
print("[11] scheduler gating + manual-stop skip OK")

# ---------- 6. app.py imports cleanly ----------
import app
print("[6] app.py import OK")
print("ALL TESTS PASSED")
