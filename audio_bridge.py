"""
RX audio bridge for IC-9700: capture the rig's USB audio codec and stream
PCM16 to web clients over WebSocket.

IC-9700 USB audio is stereo: CH1 (left) = MAIN band, CH2 (right) = SUB band.
Channel modes: "ch1" / "ch2" (mono, half bandwidth) or "stereo".
"""

import logging
import queue
import threading

logger = logging.getLogger("audio")

SR = 48000          # IC-9700 USB codec native rate
CHUNK = 960         # 20 ms frames


class AudioBridge:
    def __init__(self):
        self._stream = None
        self._q = queue.Queue(maxsize=200)
        self._lock = threading.RLock()
        self.channel = "stereo"
        self.device = None
        self.running = False

    def list_devices(self):
        """Input devices: [{index, name, channels, samplerate}]."""
        import sounddevice as sd
        out = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                out.append({"index": i, "name": d["name"],
                            "channels": d["max_input_channels"],
                            "samplerate": int(d["default_samplerate"])})
        return out

    def default_device(self):
        """Prefer the IC-9700 USB codec (WASAPI instance if present)."""
        devs = self.list_devices()
        cand = [d for d in devs if "USB Audio CODEC" in d["name"]]
        if not cand:
            return devs[0]["index"] if devs else None
        wasapi = [d for d in cand if d["samplerate"] == 48000]
        return (wasapi[0] if wasapi else cand[0])["index"]

    def _callback(self, indata, frames, time_info, status):
        try:
            if self.channel == "ch1":
                self._q.put_nowait(indata[:, 0].tobytes())
            elif self.channel == "ch2":
                self._q.put_nowait(indata[:, 1].tobytes())
            else:
                self._q.put_nowait(indata.tobytes())
        except queue.Full:
            pass  # slow client -> drop frames rather than build latency

    def start(self, device=None, channel=None):
        import sounddevice as sd
        with self._lock:
            self.stop()
            if channel:
                self.channel = channel
            if device is not None:
                self.device = int(device)
            if self.device is None:
                self.device = self.default_device()
            if self.device is None:
                raise RuntimeError("没有找到录音设备")
            self._stream = sd.InputStream(
                device=self.device, channels=2, samplerate=SR,
                dtype="int16", blocksize=CHUNK, callback=self._callback)
            self._stream.start()
            self.running = True
            logger.info("audio capture started: dev=%s channel=%s",
                        self.device, self.channel)

    def stop(self):
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            self.running = False
            with self._q.mutex:
                self._q.queue.clear()

    def read(self, timeout=0.1):
        """Next PCM16 chunk (s16le, 48 kHz, channels per mode) or None."""
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def state(self):
        return {"running": self.running, "channel": self.channel,
                "device": self.device, "samplerate": SR,
                "stream_channels": 1 if self.channel in ("ch1", "ch2") else 2}
