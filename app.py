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
    _fh = logging.FileHandler(_log_path(), encoding="utf-8")
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
from lan import LanCIVTransport

# Global state
current_transport = CIVSerial()
controller = CIVController(current_transport)
connected_ws: set[WebSocket] = set()
polling_task = None
running = True
_main_loop: Optional[asyncio.AbstractEventLoop] = None


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


def on_serial_data(msg: dict):
    """Callback for CI-V serial data."""
    cmd = msg.get("cmd")
    payload = msg.get("payload", b"")
    payload_hex = payload.hex().upper()

    # Spectrum/scope data (cmd 0x27) — not a CI-V response, silently discard
    if cmd == 0x27:
        return

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
            val = (payload[1] << 8) | payload[2]
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
        if subcmd == 0x05 and len(payload) >= 3:
            out["item"] = (payload[1] << 8) | payload[2]
            out["value"] = list(payload[3:])
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
                if sub is not None:
                    tr.send(cmd, data=bytes([sub]) if data is None else data)
                else:
                    tr.send(cmd)
            except Exception:
                pass
        idx += 1
        time.sleep(0.15)  # Poll rate


@asynccontextmanager
async def lifespan(app: FastAPI):
    global running, polling_task, _main_loop
    _main_loop = asyncio.get_running_loop()
    running = True
    polling_task = threading.Thread(target=poll_loop, daemon=True)
    polling_task.start()
    yield
    running = False
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
async def connect_port(port: str, baudrate: int = 115200):
    try:
        switch_transport(CIVSerial())
        await asyncio.to_thread(current_transport.open, port, baudrate)
        await asyncio.sleep(0.2)
        controller.read_id()
        return {"success": True, "connected": True, "mode": "serial"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/connect_lan")
async def connect_lan(host: str, username: str = "", password: str = "", control_port: int = 50001, civ_port: int = 50002):
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


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
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
                    try:
                        switch_transport(CIVSerial())
                        await asyncio.to_thread(current_transport.open, port, baud)
                        await asyncio.sleep(0.1)
                        controller.read_id()
                        await websocket.send_text(json.dumps({"type": "connection", "connected": True, "mode": "serial"}))
                    except Exception as e:
                        await websocket.send_text(json.dumps({"type": "connection", "connected": False, "error": str(e)}))

                elif action == "connect_lan":
                    host = msg.get("host", "")
                    username = msg.get("username", "")
                    password = msg.get("password", "")
                    control_port = msg.get("control_port", 50001)
                    civ_port = msg.get("civ_port", 50002)
                    try:
                        switch_transport(LanCIVTransport())
                        await asyncio.to_thread(current_transport.open, host, username, password, control_port, civ_port)
                        await asyncio.sleep(0.1)
                        controller.read_id()
                        await websocket.send_text(json.dumps({"type": "connection", "connected": True, "mode": "lan"}))
                    except Exception as e:
                        await websocket.send_text(json.dumps({"type": "connection", "connected": False, "error": str(e)}))

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

                elif action == "set_1a_05":
                    item = int(msg["item"], 0) if isinstance(msg["item"], str) else int(msg["item"])
                    val = msg["value"]
                    if isinstance(val, int):
                        if val <= 255:
                            data = bytes([val])
                        else:
                            data = bytes([(val >> 8) & 0xFF, val & 0xFF])
                    elif isinstance(val, list):
                        data = bytes(val)
                    else:
                        data = bytes([val])
                    controller.set_1a_05(item, data)

                elif action == "read_1a_05":
                    item = int(msg["item"], 0) if isinstance(msg["item"], str) else int(msg["item"])
                    controller.read_1a_05(item)

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
                            controller.read_1a_05(int(t.split("_")[1], 16))

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
                            threading.Timer(delay * 0.03, lambda i=item: controller.read_1a_05(i)).start()
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
                        delay += 1

                else:
                    await websocket.send_text(json.dumps({"type": "error", "message": f"Unknown action: {action}"}))

            except Exception as e:
                await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
    except WebSocketDisconnect:
        connected_ws.discard(websocket)
    except Exception as e:
        connected_ws.discard(websocket)


if __name__ == "__main__":
    import uvicorn

    host = "127.0.0.1"
    port = 8080

    # Parse --host / --port args (simple, no argparse needed)
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--host" and i + 1 < len(args):
            host = args[i + 1]
        elif a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])

    url = f"http://{host}:{port}"
    print(f"\n  IC-9700 CI-V 控制器")
    print(f"  浏览器访问: {url}")
    print(f"  按 Ctrl+C 退出\n")

    # Auto-open browser after a short delay (in a daemon thread)
    def _open_browser():
        time.sleep(0.8)
        webbrowser.open(url)

    threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(app, host=host, port=port)
