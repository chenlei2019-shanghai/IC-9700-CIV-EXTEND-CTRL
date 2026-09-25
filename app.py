"""
IC-9700 CI-V Web Controller
FastAPI backend with WebSocket for real-time CI-V communication.
"""

import asyncio
import json
import logging
import os
import sys
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from typing import Optional

# === Configure logging BEFORE FastAPI imports (FastAPI configures logging on import) ===

def _log_path() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "app.log")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.log")

_handlers = [logging.StreamHandler()]
try:
    # Fresh log per session (mode="w"); the file is also deleted on shutdown
    # when the last web client disconnects.
    _fh = logging.FileHandler(_log_path(), mode="w", encoding="utf-8")
    _handlers.append(_fh)
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_handlers,
    force=True,  # override any handlers set by library imports
)

logging.info("IC-9700 CI-V Controller starting — log: %s", _log_path())

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from civ import CIVSerial, CIVController, bcd_to_freq, MODES, PREAMBLE, END_CODE
from civ import set_radio_addr
from lan import LanCIVTransport
from sat_tracker import SatTracker, fetch_tle_celestrak
from audio_bridge import AudioBridge
from item_map_9700_705 import IC9700_TO_IC705 as _RAW_ITEM_MAP, IC705_BCD_ITEMS as _RAW_705_BCD


def _itemno(n: int) -> int:
    """Convert a printed 4-digit item number (stored as decimal int, e.g. 106)
    to the app's hex-style item value (0x0106)."""
    return int(str(n).zfill(4), 16)


# Item map uses printed decimal item numbers; convert to hex-style values
IC9700_TO_IC705 = {_itemno(k): _itemno(v) for k, v in _RAW_ITEM_MAP.items()}
IC705_BCD_ITEMS = {_itemno(x) for x in _RAW_705_BCD}

# Radio model state: "ic9700" or "ic705"
current_model = "ic9700"
RIG_ADDRS = {"ic9700": 0xA2, "ic705": 0xA4}

# Reverse map for translating radio responses back to UI (IC-9700) numbering.
# One IC-705 item can serve several IC-9700 items (e.g. NB level 0321/0324 ->
# 705 0356), so the reverse map is one-to-many and every matching UI control
# gets updated from a single radio response.
IC705_TO_IC9700 = {}
for _k, _v in IC9700_TO_IC705.items():
    IC705_TO_IC9700.setdefault(_v, []).append(_k)

# IC-705-only BCD extra: 0089 REF Adjust (BCD, range 0000~0511)
IC705_BCD_ITEMS_FULL = set(IC705_BCD_ITEMS) | {0x0089}

# Items whose VALUE also needs translation (9700 0050 SPEECH Language:
# 00=English,01=Japanese / 705 0053: 00=Japanese,01=English -> swap 0/1)
VALUE_SWAP_ITEMS = {0x0050}


def set_rig_model(model: str):
    """Switch target radio model (ic9700 / ic705)."""
    global current_model
    current_model = model if model in RIG_ADDRS else "ic9700"
    set_radio_addr(RIG_ADDRS[current_model])
    logging.info("Rig model: %s (CI-V addr 0x%02X)", current_model, RIG_ADDRS[current_model])


def to_radio_item(item: int) -> int:
    """Translate a UI (IC-9700) 1A05 item number to the connected radio's."""
    if current_model == "ic705":
        return IC9700_TO_IC705.get(item, item)
    return item


def to_ui_items(radio_item: int) -> list:
    """Translate a radio-reported 1A05 item number back to UI numbering.
    Returns a list because one IC-705 item may map to several UI items."""
    if current_model == "ic705":
        return IC705_TO_IC9700.get(radio_item, [radio_item])
    return [radio_item]


def is_bcd_item(radio_item: int) -> bool:
    """Check BCD encoding against the radio-side item number."""
    if current_model == "ic705":
        return radio_item in IC705_BCD_ITEMS_FULL
    return radio_item in BCD_ITEMS

# Global state
current_transport = CIVSerial()
controller = CIVController(current_transport)
connected_ws: set[WebSocket] = set()
polling_task = None
running = True
_main_loop: Optional[asyncio.AbstractEventLoop] = None
_shutdown_watchdog: Optional[asyncio.Task] = None

# Satellite Doppler tracker (dual-VFO, normal VFO mode). Uses `controller`
# indirectly via get_transport() so transport switches are picked up.
sat_tracker = SatTracker(lambda: CIVController(get_transport()), None)  # broadcast wired below

# RX audio bridge (USB codec -> PCM16 -> /ws/audio)
audio_bridge = AudioBridge()
audio_ws_clients: set[WebSocket] = set()


def get_transport():
    return current_transport


def switch_transport(new_transport):
    global current_transport, controller
    try:
        current_transport.close()
    except Exception:
        pass
    current_transport = new_transport
    controller = CIVController(current_transport)
    current_transport.set_callback(on_serial_data)


def bcd2_to_int(b1: int, b2: int) -> int:
    """Decode 2-byte BCD value (4 decimal digits) to integer."""
    return ((b1 >> 4) & 0x0F) * 1000 + (b1 & 0x0F) * 100 + ((b2 >> 4) & 0x0F) * 10 + (b2 & 0x0F)


def int_to_bcd2(val: int) -> bytes:
    """Encode 0-9999 to 2-byte BCD."""
    return bytes([
        ((val // 1000) % 10) << 4 | ((val // 100) % 10),
        ((val // 10) % 10) << 4 | (val % 10),
    ])


# CI-V items that use BCD encoding for values (range "0000 ~ 0255" in spec)
# Note: values must be sent as 2-byte BCD (e.g. 200 -> 0x02 0x00); sending a
# single raw binary byte makes the radio reject values whose nibbles are A-F
# (e.g. 127 -> 0x7F), which caused "level can't be raised after lowering".
BCD_ITEMS = {
    0x0027,  # Beep Level
    0x0031,  # Beep Sound (MAIN)
    0x0032,  # Beep Sound (SUB)
    0x0057,  # SPEECH Level
    0x0072,  # REF Adjust
    0x0073,  # REF Adjust (FINE)
    0x0086,  # EMR AF Level
    0x0101,  # ACC AF Output Level
    0x0104,  # ACC IF Output Level
    0x0106,  # USB AF Output Level
    0x0109,  # USB IF Output Level
    0x0112,  # ACC MOD Level
    0x0113,  # USB MOD Level
    0x0114,  # LAN MOD Level
    0x0152,  # LCD Backlight
    0x0215,  # VOICE TX Level
    0x0221,  # Side Tone Level
    0x0321,  # NB Level (144M)
    0x0323,  # NB Width (144M)
    0x0326,  # NB Width (430M)
    0x0329,  # NB Width (1200M)
    0x0333,  # TX PWR LIMIT (144M)
    0x0335,  # TX PWR LIMIT (430M)
    0x0337,  # TX PWR LIMIT (1200M)
}


def decode_level(payload: bytes) -> int:
    """Decode 2-byte level value."""
    if len(payload) >= 2:
        return (payload[0] << 8) | payload[1]
    return 0


def decode_mode(payload: bytes) -> dict:
    """Decode mode/filter payload."""
    if len(payload) >= 1:
        mode = MODES.get(payload[0], f"0x{payload[0]:02X}")
        filt = ""
        if len(payload) >= 2:
            filt = f"FIL{payload[1]}"
        return {"mode": mode, "filter": filt}
    return {}


def decode_frequency(payload: bytes) -> int:
    return bcd_to_freq(payload)


def broadcast(msg: dict):
    """Broadcast message to all connected WebSockets."""
    if not connected_ws:
        return
    data = json.dumps(msg)
    loop = _main_loop
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
    for ws in list(connected_ws):
        try:
            asyncio.run_coroutine_threadsafe(ws.send_text(data), loop)
        except Exception:
            pass


sat_tracker._broadcast = broadcast


def on_serial_data(msg: dict):
    """Callback for CI-V serial data."""
    cmd = msg.get("cmd")
    payload = msg.get("payload", b"")
    payload_hex = payload.hex().upper()

    # Spectrum/scope data (cmd 0x27) — not a CI-V response, silently discard
    if cmd == 0x27:
        return

    # Feed all responses to the satellite tracker (freq reads, state queries)
    if cmd is not None:
        try:
            sat_tracker.feed_response(cmd, payload)
        except Exception:
            pass

    out = {"type": "civ_response", "cmd": cmd, "payload_hex": payload_hex}

    if cmd == 0x00:
        out["event"] = "frequency"
        out["frequency"] = decode_frequency(payload)
    elif cmd == 0x03:
        out["event"] = "frequency"
        out["frequency"] = decode_frequency(payload)
    elif cmd == 0x01 or cmd == 0x04 or cmd == 0x06:
        out["event"] = "mode"
        out.update(decode_mode(payload))
    elif cmd == 0x14:
        if len(payload) >= 3:
            sub = payload[0]
            val = bcd2_to_int(payload[1], payload[2])  # levels are 2-byte BCD
            out["event"] = "level"
            out["subcmd"] = sub
            out["value"] = val
    elif cmd == 0x15:
        if len(payload) >= 3:
            sub = payload[0]
            val = bcd2_to_int(payload[1], payload[2])
            out["event"] = "meter"
            out["subcmd"] = sub
            out["value"] = val
    elif cmd == 0x16:
        if len(payload) >= 2:
            sub = payload[0]
            val = payload[1]
            out["event"] = "function"
            out["subcmd"] = sub
            out["value"] = val
    elif cmd == 0x0F:
        if len(payload) >= 1:
            out["event"] = "split_duplex"
            out["value"] = payload[0]
    elif cmd == 0x10:
        if len(payload) >= 1:
            out["event"] = "tuning_step"
            out["value"] = payload[0]
    elif cmd == 0x11:
        if len(payload) >= 1:
            out["event"] = "attenuator"
            out["value"] = payload[0]
    elif cmd == 0x1C:
        if len(payload) >= 1:
            if len(payload) >= 2 and payload[0] == 0x02:
                out["event"] = "xfc"
                out["value"] = payload[1]
            else:
                out["event"] = "tx_status"
                out["value"] = payload[0]
    elif cmd == 0x1A:
        out["event"] = "extended"
        subcmd = payload[0] if len(payload) >= 1 else 0
        if subcmd == 0x04:  # Extended AGC time constant
            out["item"] = 0x1A04
            out["value"] = payload[1] if len(payload) >= 2 else 0
        elif subcmd == 0x05 and len(payload) >= 3:
            radio_item = (payload[1] << 8) | payload[2]
            data = payload[3:]
            if is_bcd_item(radio_item) and len(data) >= 2:
                value = bcd2_to_int(data[0], data[1])
            else:
                value = list(data)
            # One radio item may feed several UI controls (IC-705 shared items)
            for item in to_ui_items(radio_item):
                v = value
                if item in VALUE_SWAP_ITEMS and isinstance(value, list) and value and value[0] in (0, 1):
                    v = 1 - value[0]  # SPEECH Language 0/1 reversed on IC-705
                broadcast(dict(out, item=item, value=v))
            return
    elif cmd == 0x1B:
        out["event"] = "tone"
        out["payload"] = payload_hex
    elif cmd == 0x19:
        out["event"] = "id"
        out["payload"] = payload_hex
    elif cmd == 0x21:
        if len(payload) >= 1:
            out["event"] = "rit"
            out["value"] = payload[0]
    elif cmd == 0x24:
        if len(payload) >= 2:
            out["event"] = "tx_power_setting"
            out["value"] = payload[1]

    broadcast(out)


current_transport.set_callback(on_serial_data)


def poll_loop():
    """Background thread to poll radio status."""
    # Poll sequence
    poll_commands = [
        (0x03, None, None),   # Frequency
        (0x04, None, None),   # Mode
        (0x15, 0x02, None),   # S-meter
        (0x15, 0x11, None),   # PO
        (0x15, 0x12, None),   # SWR
        (0x15, 0x15, None),   # Vd
        (0x15, 0x16, None),   # Id
        (0x1C, 0x00, None),   # TX/RX status
    ]
    idx = 0
    while running:
        tr = get_transport()
        if tr.is_open():
            try:
                cmd, sub, data = poll_commands[idx % len(poll_commands)]
                # While the sat tracker runs it owns band selection and freq
                # reads; skip freq/mode polls to avoid CI-V interleaving.
                if not (sat_tracker.running and cmd in (0x03, 0x04)):
                    if sub is not None:
                        tr.send(cmd, data=bytes([sub]) if data is None else data)
                    else:
                        tr.send(cmd)
            except Exception:
                pass
        idx += 1
        time.sleep(0.15)  # Poll rate


def _settings_path() -> str:
    """Connection settings file: next to the EXE when frozen, else next to app.py."""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "connect_settings.json")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "connect_settings.json")


def load_connect_settings() -> dict:
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_connect_settings(s: dict):
    try:
        cur = load_connect_settings()
        cur.update(s)  # merge: keep unrelated keys (e.g. sat_auto_tle)
        with open(_settings_path(), "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    global running, polling_task, _main_loop
    _main_loop = asyncio.get_running_loop()
    running = True
    polling_task = threading.Thread(target=poll_loop, daemon=True)
    polling_task.start()
    asyncio.create_task(audio_pump())

    # Load persisted TLE cache; optionally auto-update in the background
    sat_tracker.load_tle_cache()
    sat_tracker.load_transp_cache()
    _st = load_connect_settings()
    sat_tracker.favs = _st.get("sat_favs", [])
    if _st.get("sat_schedule"):
        sat_tracker.set_schedule(True)
    sat_tracker.auto_tle = bool(_st.get("sat_auto_tle", False))
    sat_tracker.tle_url = _st.get("tle_url") or None
    if _st.get("rot_on"):
        sat_tracker.set_rotator(on=True, host=_st.get("rot_host"),
                                port=_st.get("rot_port"))
    if sat_tracker.auto_tle:
        def _auto_tle():
            try:
                tle = fetch_tle_celestrak(sat_tracker.tle_url)
                sat_tracker.set_tle_bulk(tle)
                logging.info("TLE auto-update OK: %d sats", len(tle))
                sat_tracker._broadcast_status()
            except Exception as e:
                logging.warning("TLE auto-update failed: %s", e)
        threading.Thread(target=_auto_tle, daemon=True).start()

    yield
    running = False
    try:
        if sat_tracker.running:
            sat_tracker.stop()
    except Exception:
        pass
    try:
        current_transport.close()
    except Exception:
        pass


app = FastAPI(lifespan=lifespan)


def _static_dir() -> str:
    """Resolve static directory for both dev and PyInstaller modes."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", "")
        path = os.path.join(base, "static")
        if os.path.isdir(path):
            return path
        # fallback: try relative to the exe
        path = os.path.join(os.path.dirname(sys.executable), "static")
        if os.path.isdir(path):
            return path
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


app.mount("/static", StaticFiles(directory=_static_dir()), name="static")


@app.get("/")
async def root():
    return FileResponse(os.path.join(_static_dir(), "index.html"))


@app.get("/api/ports")
async def get_ports():
    return {"ports": CIVSerial().list_ports()}


@app.get("/api/status")
async def get_status():
    return {"connected": current_transport.is_open()}


@app.post("/api/connect")
async def connect_port(port: str, baudrate: int = 115200, model: str = "ic9700"):
    set_rig_model(model)
    try:
        switch_transport(CIVSerial())
        await asyncio.to_thread(current_transport.open, port, baudrate)
        await asyncio.sleep(0.2)
        controller.read_id()
        return {"success": True, "connected": True, "mode": "serial"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/connect_lan")
async def connect_lan(host: str, username: str = "", password: str = "", control_port: int = 50001, civ_port: int = 50002, model: str = "ic9700"):
    set_rig_model(model)
    # IC-705 支持通过 WLAN 走同一 Icom UDP 协议（wfview 兼容），不拦截
    try:
        switch_transport(LanCIVTransport())
        await asyncio.to_thread(current_transport.open, host, username, password, control_port, civ_port)
        await asyncio.sleep(0.2)
        controller.read_id()
        return {"success": True, "connected": True, "mode": "lan"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/disconnect")
async def disconnect_port():
    try:
        current_transport.close()
    except Exception:
        pass
    return {"success": True, "connected": False}


async def _shutdown_after_grace(grace_s: float = 8.0):
    """When the last web client disconnects, shut down the backend after a
    grace period (cancelled if a client reconnects, e.g. page refresh) and
    delete this session's log file."""
    global running
    try:
        await asyncio.sleep(grace_s)
    except asyncio.CancelledError:
        return
    if connected_ws:
        return
    logging.info("last web client disconnected — shutting down backend")
    running = False
    _do_shutdown()


def _do_shutdown():
    """Stop tracker (restores radio), close transport, delete session log, exit."""
    def _cleanup_and_exit():
        try:
            if sat_tracker.running:
                sat_tracker.stop()  # also restores radio state
        except Exception:
            pass
        try:
            audio_bridge.stop()
        except Exception:
            pass
        try:
            current_transport.close()
        except Exception:
            pass
        lp = _log_path()
        for h in logging.root.handlers[:]:  # release the log file (Windows)
            try:
                h.close()
            except Exception:
                pass
            logging.root.removeHandler(h)
        try:
            os.remove(lp)
        except Exception:
            pass
        os._exit(0)

    threading.Thread(target=_cleanup_and_exit, daemon=True).start()


def _on_ws_closed(ws: WebSocket):
    global _shutdown_watchdog
    connected_ws.discard(ws)
    if not connected_ws and _main_loop is not None:
        if _shutdown_watchdog is None or _shutdown_watchdog.done():
            _shutdown_watchdog = asyncio.run_coroutine_threadsafe(
                _shutdown_after_grace(), _main_loop)


async def audio_pump():
    """Push captured PCM16 chunks to all /ws/audio clients."""
    while True:
        try:
            chunk = await asyncio.to_thread(audio_bridge.read, 0.2)
        except Exception:
            chunk = None
        if chunk is None:
            continue
        dead = []
        for ws in list(audio_ws_clients):
            try:
                await ws.send_bytes(chunk)
            except Exception:
                dead.append(ws)
        for ws in dead:
            audio_ws_clients.discard(ws)


@app.websocket("/ws/audio")
async def audio_ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    audio_ws_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()  # keepalive / ignore
    except Exception:
        pass
    finally:
        audio_ws_clients.discard(websocket)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global _shutdown_watchdog, running
    await websocket.accept()
    if _shutdown_watchdog is not None and not _shutdown_watchdog.done():
        _shutdown_watchdog.cancel()  # client (re)connected, stay alive
    connected_ws.add(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                action = msg.get("action")
                # Log all user actions
                if action != "poll_panel":  # batch reads are noisy, skip details
                    logging.info("WS action: %s %s",
                        action, json.dumps({k: v for k, v in msg.items() if k != "action"}))
                elif msg.get("reads"):
                    logging.info("WS action: poll_panel reads=%d", len(msg["reads"]))

                if action == "connect":
                    port = msg.get("port", "COM1")
                    baud = msg.get("baudrate", 115200)
                    set_rig_model(msg.get("model", "ic9700"))
                    try:
                        switch_transport(CIVSerial())
                        await asyncio.to_thread(current_transport.open, port, baud)
                        await asyncio.sleep(0.1)
                        controller.read_id()
                        save_connect_settings({"mode": "serial", "port": port,
                                               "baudrate": baud, "model": current_model})
                        await websocket.send_text(json.dumps({"type": "connection", "connected": True, "mode": "serial", "model": current_model}))
                    except Exception as e:
                        logging.warning("serial connect failed (%s): %s", port, e)
                        await websocket.send_text(json.dumps({"type": "connection", "connected": False, "error": str(e)}))

                elif action == "connect_lan":
                    host = msg.get("host", "")
                    username = msg.get("username", "")
                    password = msg.get("password", "")
                    control_port = msg.get("control_port", 50001)
                    civ_port = msg.get("civ_port", 50002)
                    set_rig_model(msg.get("model", "ic9700"))
                    # IC-705 支持通过 WLAN 走同一 Icom UDP 协议（wfview 兼容），不拦截
                    try:
                        switch_transport(LanCIVTransport())
                        await asyncio.to_thread(current_transport.open, host, username, password, control_port, civ_port)
                        await asyncio.sleep(0.1)
                        controller.read_id()
                        save_connect_settings({"mode": "lan", "host": host,
                                               "username": username, "password": password,
                                               "control_port": control_port, "civ_port": civ_port,
                                               "model": current_model})
                        await websocket.send_text(json.dumps({"type": "connection", "connected": True, "mode": "lan", "model": current_model}))
                    except Exception as e:
                        logging.warning("LAN connect failed (%s): %s", host, e)
                        await websocket.send_text(json.dumps({"type": "connection", "connected": False, "error": str(e)}))

                elif action == "get_settings":
                    await websocket.send_text(json.dumps(
                        {"type": "connect_settings", "settings": load_connect_settings()}))

                elif action == "shutdown":
                    logging.info("shutdown requested from web UI")
                    running = False
                    await websocket.send_text(json.dumps({"type": "shutdown", "bye": True}))
                    _do_shutdown()

                # ===== RX audio bridge =====
                elif action == "audio_devices":
                    try:
                        devs = await asyncio.to_thread(audio_bridge.list_devices)
                        await websocket.send_text(json.dumps(
                            {"type": "audio_devices", "devices": devs,
                             "default": audio_bridge.default_device()}))
                    except Exception as e:
                        await websocket.send_text(json.dumps(
                            {"type": "audio_devices", "devices": [], "error": str(e)}))

                elif action == "audio_start":
                    try:
                        await asyncio.to_thread(audio_bridge.start,
                                                msg.get("device"), msg.get("channel"))
                    except Exception as e:
                        await websocket.send_text(json.dumps(
                            {"type": "audio_state", "error": str(e)}))
                    broadcast({"type": "audio_state", **audio_bridge.state()})

                elif action == "audio_stop":
                    await asyncio.to_thread(audio_bridge.stop)
                    broadcast({"type": "audio_state", **audio_bridge.state()})

                elif action == "audio_set_channel":
                    try:
                        await asyncio.to_thread(audio_bridge.start, None,
                                                msg.get("channel", "stereo"))
                    except Exception as e:
                        await websocket.send_text(json.dumps(
                            {"type": "audio_state", "error": str(e)}))
                    broadcast({"type": "audio_state", **audio_bridge.state()})

                elif action == "disconnect":
                    try:
                        current_transport.close()
                    except Exception:
                        pass
                    await websocket.send_text(json.dumps({"type": "connection", "connected": False}))

                elif action == "set_frequency":
                    controller.set_frequency(int(msg["freq"]))

                elif action == "set_mode":
                    mode = msg["mode"]
                    filt = msg.get("filter", 1)
                    if isinstance(mode, str):
                        from civ import MODES_REV
                        mode = MODES_REV.get(mode, 0x01)
                    controller.set_mode(mode, filt)

                elif action == "vfo":
                    vfo = msg.get("vfo")
                    if vfo == "A":
                        controller.vfo_a()
                    elif vfo == "B":
                        controller.vfo_b()
                    elif vfo == "equal":
                        controller.vfo_equal()
                    elif vfo == "exchange":
                        controller.vfo_exchange()
                    elif vfo == "main":
                        controller.select_main()
                    elif vfo == "sub":
                        controller.select_sub()

                elif action == "memory":
                    controller.select_memory(int(msg["channel"]))

                elif action == "scan":
                    scan_type = msg.get("type", "cancel")
                    mapping = {
                        "cancel": 0x00, "pm": 0x01, "p": 0x02, "df": 0x03,
                        "fine_p": 0x12, "fine_df": 0x13, "mem": 0x22,
                        "sel_mem": 0x23, "mode_sel": 0x24
                    }
                    controller.scan(mapping.get(scan_type, 0x00))

                elif action == "set_split":
                    controller.set_split(msg.get("on", False))

                elif action == "set_duplex":
                    duplex = msg.get("duplex", "simplex")
                    mapping = {"simplex": 0x10, "dup-": 0x11, "dup+": 0x12, "rps": 0x13}
                    controller.set_duplex(mapping.get(duplex, 0x10))

                elif action == "set_tuning_step":
                    controller.set_tuning_step(int(msg["step"]))

                elif action == "set_attenuator":
                    controller.set_attenuator(int(msg["value"]))

                elif action == "set_level":
                    sub = int(msg["subcmd"], 0) if isinstance(msg["subcmd"], str) else int(msg["subcmd"])
                    controller.set_level(sub, int(msg["value"]))

                elif action == "read_level":
                    sub = int(msg["subcmd"], 0) if isinstance(msg["subcmd"], str) else int(msg["subcmd"])
                    controller.read_level(sub)

                elif action == "set_function":
                    sub = int(msg["subcmd"], 0) if isinstance(msg["subcmd"], str) else int(msg["subcmd"])
                    controller.set_function(sub, int(msg["value"]))

                elif action == "read_function":
                    sub = int(msg["subcmd"], 0) if isinstance(msg["subcmd"], str) else int(msg["subcmd"])
                    controller.read_function(sub)

                elif action == "set_rit":
                    controller.set_rit(msg.get("on", False))

                elif action == "set_rit_freq":
                    controller.set_rit_freq(int(msg["freq"]), 0x01 if msg.get("direction") == "-" else 0x00)

                elif action == "set_xfc":
                    controller.set_xfc(msg.get("on", False))

                elif action == "power":
                    if msg.get("on"):
                        controller.power_on()
                    else:
                        controller.power_off()

                elif action == "read_meter":
                    sub = int(msg["subcmd"], 0) if isinstance(msg["subcmd"], str) else int(msg["subcmd"])
                    controller.read_meter(sub)

                elif action == "read_tx_power_setting":
                    controller.read_tx_power_setting()

                elif action == "set_ext_agc":
                    controller.set_ext_agc(int(msg["value"]))

                elif action == "read_ext_agc":
                    controller.read_ext_agc()

                elif action == "set_1a_05":
                    item = int(msg["item"], 0) if isinstance(msg["item"], str) else int(msg["item"])
                    val = msg["value"]
                    ritem = to_radio_item(item)
                    if isinstance(val, int):
                        if item in VALUE_SWAP_ITEMS and val in (0, 1):
                            val = 1 - val  # SPEECH Language 0/1 reversed on IC-705
                        if is_bcd_item(ritem):
                            data = int_to_bcd2(val)
                        elif val <= 255:
                            data = bytes([val])
                        else:
                            data = bytes([(val >> 8) & 0xFF, val & 0xFF])
                    elif isinstance(val, list):
                        data = bytes(val)
                    else:
                        data = bytes([val])
                    controller.set_1a_05(ritem, data)

                elif action == "read_1a_05":
                    item = int(msg["item"], 0) if isinstance(msg["item"], str) else int(msg["item"])
                    controller.read_1a_05(to_radio_item(item))

                elif action == "set_scan_resume":
                    controller.set_scan_resume(msg.get("on", False))

                elif action == "set_scan_span":
                    controller.set_scan_span(int(msg["span"]))

                elif action == "voice_tx":
                    controller.voice_tx_memory(int(msg["channel"]))

                elif action == "raw":
                    # Send raw hex string
                    raw_hex = msg.get("data", "")
                    data = bytes.fromhex(raw_hex.replace(" ", ""))
                    current_transport.send_raw(data)

                # ===== Satellite Doppler tracking (dual-VFO) =====
                elif action == "sat_state":
                    await websocket.send_text(json.dumps(sat_tracker.get_state()))

                elif action == "sat_fetch_tle":
                    try:
                        url = (msg.get("url") or "").strip() or None
                        if url:
                            sat_tracker.tle_url = url
                            save_connect_settings({"tle_url": url})
                        tle = await asyncio.to_thread(fetch_tle_celestrak, sat_tracker.tle_url)
                        sat_tracker.set_tle_bulk(tle)
                        # re-match in case TLE arrived after configure
                        if sat_tracker.cfg:
                            from sat_tracker import match_tle_name
                            sat_tracker.tle_name = match_tle_name(tle.keys(), sat_tracker.cfg["name"])
                        await websocket.send_text(json.dumps(
                            {"type": "sat_tle", "success": True, "count": len(tle)}))
                    except Exception as e:
                        await websocket.send_text(json.dumps(
                            {"type": "sat_tle", "success": False, "error": str(e)}))
                    await websocket.send_text(json.dumps(sat_tracker.get_state()))

                elif action == "sat_rotator":
                    sat_tracker.set_rotator(on=msg.get("on"), host=msg.get("host"),
                                            port=msg.get("port"))
                    save_connect_settings({
                        "rot_on": sat_tracker.rotator["on"],
                        "rot_host": sat_tracker.rotator["host"],
                        "rot_port": sat_tracker.rotator["port"]})
                    sat_tracker._broadcast_status()

                elif action == "sat_fav_add":
                    cfg = msg.get("cfg") or {}
                    if cfg.get("name") and cfg.get("up") and cfg.get("down"):
                        sat_tracker.fav_add(cfg)
                        save_connect_settings({"sat_favs": sat_tracker.favs})

                elif action == "sat_fav_del":
                    sat_tracker.fav_del(msg.get("name", ""))
                    save_connect_settings({"sat_favs": sat_tracker.favs})

                elif action == "sat_schedule":
                    sat_tracker.set_schedule(bool(msg.get("on", False)))
                    save_connect_settings({"sat_schedule": sat_tracker.schedule_on})

                elif action == "sat_fav_passes":
                    min_el = float(msg.get("min_el", 0))
                    hours = float(msg.get("hours", 24))
                    rows = await asyncio.to_thread(sat_tracker.fav_pass_table, hours, min_el)
                    await websocket.send_text(json.dumps(
                        {"type": "sat_fav_passes", "rows": rows}))

                elif action == "sat_get_transp":
                    await websocket.send_text(json.dumps({
                        "type": "sat_transp", "db": sat_tracker.transp_db,
                        "time": sat_tracker.transp_time}))

                elif action == "sat_fetch_transp":
                    from sat_tracker import fetch_satnogs_db
                    try:
                        db = await asyncio.to_thread(fetch_satnogs_db)
                        sat_tracker.set_transp_db(db)
                        await websocket.send_text(json.dumps(
                            {"type": "sat_transp", "success": True, "db": db,
                             "time": sat_tracker.transp_time}))
                    except Exception as e:
                        await websocket.send_text(json.dumps(
                            {"type": "sat_transp", "success": False, "error": str(e)}))

                elif action == "sat_set_tle":
                    sat_tracker.set_tle(msg["name"], msg["line1"], msg["line2"])
                    await websocket.send_text(json.dumps(sat_tracker.get_state()))

                elif action == "sat_set_observer":
                    sat_tracker.set_observer(msg.get("lat", 0), msg.get("lon", 0), msg.get("alt_m", 50))
                    await websocket.send_text(json.dumps(sat_tracker.get_state()))

                elif action == "sat_configure":
                    try:
                        sat_tracker.restore_on_stop = bool(msg.get("restore", True))
                        sat_tracker.configure(
                            name=msg["name"], up=int(msg["up"]),
                            up_mode=msg.get("up_mode", "USB"),
                            tone=msg.get("tone"),
                            down=int(msg["down"]),
                            down_mode=msg.get("down_mode", "USB"),
                            invert=msg.get("invert", False),
                            fm_step_hz=msg.get("fm_step_hz"),
                            swap=msg.get("swap", False),
                            norad=msg.get("norad"))
                        await websocket.send_text(json.dumps(
                            {"type": "sat_cfg", "success": True,
                             "tle_name": sat_tracker.tle_name}))
                    except Exception as e:
                        await websocket.send_text(json.dumps(
                            {"type": "sat_cfg", "success": False, "error": str(e)}))
                    await websocket.send_text(json.dumps(sat_tracker.get_state()))

                elif action == "sat_start":
                    try:
                        await asyncio.to_thread(sat_tracker.start)
                        await websocket.send_text(json.dumps({"type": "sat_run", "running": True}))
                    except Exception as e:
                        sat_tracker.running = False
                        await websocket.send_text(json.dumps(
                            {"type": "sat_run", "running": False, "error": str(e)}))

                elif action == "sat_stop":
                    await asyncio.to_thread(sat_tracker.stop)
                    await websocket.send_text(json.dumps({"type": "sat_run", "running": False}))

                elif action == "sat_enable":
                    sat_tracker.enabled = bool(msg.get("on", True))
                    if not sat_tracker.enabled:
                        sat_tracker.last_down = None  # avoid LOCK jump after resume
                    sat_tracker._broadcast_status()

                elif action == "sat_lock":
                    sat_tracker.lock_vfo = bool(msg.get("on", True))
                    sat_tracker.last_down = None
                    sat_tracker._broadcast_status()

                elif action == "sat_nudge":
                    sat_tracker.nudge(msg.get("which", "down"), int(msg.get("delta_hz", 0)))

                elif action == "sat_center":
                    sat_tracker.center()

                elif action == "sat_pass":
                    p = await asyncio.to_thread(sat_tracker.predict_pass)
                    await websocket.send_text(json.dumps({"type": "sat_pass", "pass": p}))

                elif action == "sat_pass_profile":
                    idx = int(msg.get("index", 0))
                    p = await asyncio.to_thread(sat_tracker.predict_pass_profile, 48.0, idx)
                    await websocket.send_text(json.dumps({"type": "sat_pass_profile", "profile": p}))

                elif action == "sat_pass_list":
                    pl = await asyncio.to_thread(sat_tracker.predict_passes, 48.0)
                    await websocket.send_text(json.dumps({"type": "sat_pass_list", "passes": pl}))

                elif action == "sat_grid":
                    from sat_tracker import grid_to_latlon
                    try:
                        lat, lon = grid_to_latlon(msg.get("grid", ""))
                        sat_tracker.set_observer(lat, lon, float(msg.get("alt_m", 50)))
                        await websocket.send_text(json.dumps(
                            {"type": "sat_grid", "success": True,
                             "lat": round(lat, 5), "lon": round(lon, 5)}))
                    except Exception as e:
                        await websocket.send_text(json.dumps(
                            {"type": "sat_grid", "success": False, "error": str(e)}))
                    await websocket.send_text(json.dumps(sat_tracker.get_state()))

                elif action == "sat_set_auto_tle":
                    sat_tracker.auto_tle = bool(msg.get("on", False))
                    save_connect_settings({"sat_auto_tle": sat_tracker.auto_tle})
                    sat_tracker._broadcast_status()

                elif action == "poll":
                    # Manual poll requests
                    poll_targets = msg.get("targets", [])
                    for t in poll_targets:
                        if t == "freq":
                            controller.read_frequency()
                        elif t == "mode":
                            controller.read_mode()
                        elif t == "smeter":
                            controller.read_smeter()
                        elif t == "tx_status":
                            controller.read_tx_status()
                        elif t == "sat_freqs":
                            controller.read_frequency()
                            controller.read_mode()
                        elif t == "sat_sub_freqs":
                            controller.ser.send(0x07, data=bytes([0xD2, 0x01]))  # read sub band
                        elif t == "split":
                            controller.read_split()
                        elif t == "tuning_step":
                            controller.read_tuning_step()
                        elif t == "attenuator":
                            controller.read_attenuator()
                        elif t == "xfc":
                            controller.read_xfc()
                        elif t == "tx_power_setting":
                            controller.read_tx_power_setting()
                        elif t == "rit":
                            controller.ser.send(0x21, data=bytes([0x01]))
                        elif t.startswith("level_"):
                            controller.read_level(int(t.split("_")[1], 16))
                        elif t.startswith("func_"):
                            controller.read_function(int(t.split("_")[1], 16))
                        elif t.startswith("1a_"):
                            controller.read_1a_05(to_radio_item(int(t.split("_")[1], 16)))

                elif action == "poll_panel":
                    reads = msg.get("reads", [])
                    import time as _time
                    delay = 0
                    for r in reads:
                        typ = r.get("type", "")
                        sub = r.get("subcmd")
                        item = r.get("item")
                        tgt = r.get("target", "")
                        if typ == "level" and sub is not None:
                            threading.Timer(delay * 0.03, lambda s=sub: controller.read_level(s)).start()
                        elif typ == "function" and sub is not None:
                            threading.Timer(delay * 0.03, lambda s=sub: controller.read_function(s)).start()
                        elif typ == "1a_05" and item is not None:
                            threading.Timer(delay * 0.03, lambda i=item: controller.read_1a_05(to_radio_item(i))).start()
                        elif typ == "special":
                            if tgt == "freq":
                                threading.Timer(delay * 0.03, controller.read_frequency).start()
                            elif tgt == "mode":
                                threading.Timer(delay * 0.03, controller.read_mode).start()
                            elif tgt == "smeter":
                                threading.Timer(delay * 0.03, controller.read_smeter).start()
                            elif tgt == "tx_status":
                                threading.Timer(delay * 0.03, controller.read_tx_status).start()
                            elif tgt == "split":
                                threading.Timer(delay * 0.03, controller.read_split).start()
                            elif tgt == "tuning_step":
                                threading.Timer(delay * 0.03, controller.read_tuning_step).start()
                            elif tgt == "attenuator":
                                threading.Timer(delay * 0.03, controller.read_attenuator).start()
                            elif tgt == "xfc":
                                threading.Timer(delay * 0.03, controller.read_xfc).start()
                            elif tgt == "tx_power_setting":
                                threading.Timer(delay * 0.03, controller.read_tx_power_setting).start()
                            elif tgt == "ext_agc":
                                threading.Timer(delay * 0.03, controller.read_ext_agc).start()
                        delay += 1

                else:
                    await websocket.send_text(json.dumps({"type": "error", "message": f"Unknown action: {action}"}))

            except Exception as e:
                await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
    except WebSocketDisconnect:
        _on_ws_closed(websocket)
    except Exception as e:
        _on_ws_closed(websocket)


if __name__ == "__main__":
    import uvicorn

    def _hide_console():
        """Hide the console/log window for the packaged EXE.

        Logs still go to app.log via FileHandler; only the visible window
        is hidden so end users are not distracted by it. Dev mode
        (python app.py) keeps the console visible.
        """
        if not getattr(sys, "frozen", False):
            return
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
                logging.info("Console window hidden (frozen EXE mode)")
        except Exception:
            pass

    _hide_console()

    host = "127.0.0.1"
    port = 8080

    # Parse --host / --port / --no-browser args (simple, no argparse needed)
    args = sys.argv[1:]
    no_browser = "--no-browser" in args
    for i, a in enumerate(args):
        if a == "--host" and i + 1 < len(args):
            host = args[i + 1]
        elif a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])

    # If the port is taken (e.g. an older instance is still running),
    # fall forward to the next free port instead of dying silently.
    import socket
    for _ in range(20):
        with socket.socket() as _s:
            try:
                _s.bind((host, port))
                break
            except OSError:
                port += 1

    url = f"http://{host}:{port}"
    print(f"\n  IC-9700/IC-705 CI-V 控制器")
    print(f"  浏览器访问: {url}")
    print(f"  按 Ctrl+C 退出\n")

    # Auto-open browser after a short delay (in a daemon thread)
    def _open_browser():
        time.sleep(0.8)
        webbrowser.open(url)

    if not no_browser:
        threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(app, host=host, port=port)
