# IC-9700 CI-V Web Controller

IC-9700 CI-V Web Controller is a FastAPI-based browser controller for Icom IC-9700 radios. It supports serial CI-V and Icom LAN UDP control, with a Chinese web UI for frequency, mode, VFO, memory, scan, transmit, receiver, connector, network, display, and raw CI-V operations.

## Features

- Browser UI served from `app.py` with real-time WebSocket updates
- Serial CI-V transport via `civ.py`
- Icom LAN UDP transport via `lan.py`
- Dynamic frontend in `static/app.js`, `static/index.html`, and `static/style.css`
- Windows executable build through GitHub Actions

## Quick Start

```bash
pip install fastapi uvicorn pyserial
python app.py
```

Then open `http://127.0.0.1:8080`.

## Windows Build

Run the GitHub Actions workflow `Build Windows EXE`, or build locally on Windows:

```bat
build.bat
```

The expected output is `dist\ic9700-ctrl.exe`.

## Project Files

| File | Purpose |
| --- | --- |
| `app.py` | FastAPI backend and WebSocket command dispatcher |
| `civ.py` | CI-V frame encoding, decoding, serial transport, and controller commands |
| `lan.py` | Icom LAN UDP discovery, authentication, keepalive, and CI-V tunnel |
| `sat.py` | Satellite Doppler helper module used by the web controller |
| `static/` | Browser UI assets |
| `build.bat` | Windows PyInstaller build helper |
| `.github/workflows/build-windows.yml` | CI build for the Windows executable |

## Notes

This repository is only for the IC-9700 CI-V Web Controller. GPredict satellite proxy tools are maintained separately.
