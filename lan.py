"""
Icom LAN UDP Protocol transport for IC-9700.
Implements discovery, authentication, keep-alive, and CI-V tunneling
over UDP ports 50001 (control) and 50002 (CI-V serial).

Reference: rigplane / wfview reverse-engineered protocol.
"""

import asyncio
import struct
import socket
import time
import threading
import logging
from collections import OrderedDict
from typing import Callable, Optional

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# CI-V constants used in frame parsing
PREAMBLE = bytes([0xFE, 0xFE])
END_CODE = 0xFD

# ------------------------------------------------------------------
# Protocol constants
# ------------------------------------------------------------------
HEADER_SIZE = 0x10
CONTROL_SIZE = 0x10
PING_SIZE = 0x15

PTYPE_DATA = 0x00
PTYPE_CONTROL = 0x01
PTYPE_ARE_YOU_THERE = 0x03
PTYPE_I_AM_HERE = 0x04
PTYPE_DISCONNECT = 0x05
PTYPE_ARE_YOU_READY = 0x06
PTYPE_PING = 0x07

# ------------------------------------------------------------------
# Credential encoding (wfview icomudpbase.h passcode)
# ------------------------------------------------------------------
_PASSCODE_SEQ: bytes = bytes(
    [
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        0x47, 0x5D, 0x4C, 0x42, 0x66, 0x20, 0x23, 0x46, 0x4E, 0x57,
        0x45, 0x3D, 0x67, 0x76, 0x60, 0x41, 0x62, 0x39, 0x59, 0x2D,
        0x68, 0x7E, 0x7C, 0x65, 0x7D, 0x49, 0x29, 0x72, 0x73, 0x78,
        0x21, 0x6E, 0x5A, 0x5E, 0x4A, 0x3E, 0x71, 0x2C, 0x2A, 0x54,
        0x3C, 0x3A, 0x63, 0x4F, 0x43, 0x75, 0x27, 0x79, 0x5B, 0x35,
        0x70, 0x48, 0x6B, 0x56, 0x6F, 0x34, 0x32, 0x6C, 0x30, 0x61,
        0x6D, 0x7B, 0x2F, 0x4B, 0x64, 0x38, 0x2B, 0x2E, 0x50, 0x40,
        0x3F, 0x55, 0x33, 0x37, 0x25, 0x77, 0x24, 0x26, 0x74, 0x6A,
        0x28, 0x53, 0x4D, 0x69, 0x22, 0x5C, 0x44, 0x31, 0x36, 0x58,
        0x3B, 0x7A, 0x51, 0x5F, 0x52,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    ]
)


def _encode_credentials(text: str) -> bytes:
    result = bytearray()
    for i, ch in enumerate(text[:16]):
        p = ord(ch) + i
        if p > 126:
            p = 32 + (p % 127)
        result.append(_PASSCODE_SEQ[p])
    return bytes(result)


# ------------------------------------------------------------------
# Low-level packet builders
# ------------------------------------------------------------------
def _build_control(sender_id: int, receiver_id: int, ptype: int, seq: int = 0) -> bytes:
    pkt = bytearray(CONTROL_SIZE)
    struct.pack_into("<I", pkt, 0, CONTROL_SIZE)
    struct.pack_into("<H", pkt, 4, ptype)
    struct.pack_into("<H", pkt, 6, seq)
    struct.pack_into("<I", pkt, 8, sender_id)
    struct.pack_into("<I", pkt, 0x0C, receiver_id)
    return bytes(pkt)


def _build_ping(sender_id: int, receiver_id: int, ping_seq: int) -> bytes:
    pkt = bytearray(PING_SIZE)
    struct.pack_into("<I", pkt, 0, PING_SIZE)
    struct.pack_into("<H", pkt, 4, PTYPE_PING)
    struct.pack_into("<H", pkt, 6, ping_seq)
    struct.pack_into("<I", pkt, 8, sender_id)
    struct.pack_into("<I", pkt, 0x0C, receiver_id)
    pkt[0x10] = 0x00  # request
    ms = int(time.monotonic() * 1000) & 0xFFFFFFFF
    struct.pack_into("<I", pkt, 0x11, ms)
    return bytes(pkt)


def _build_login_packet(
    username: str,
    password: str,
    sender_id: int,
    receiver_id: int,
    tok_request: int = 0,
    auth_seq: int = 0,
    computer_name: str = "IC9700Ctrl",
) -> bytes:
    pkt = bytearray(0x80)
    struct.pack_into("<I", pkt, 0x00, 0x80)
    struct.pack_into("<H", pkt, 0x04, PTYPE_DATA)
    struct.pack_into("<I", pkt, 0x08, sender_id)
    struct.pack_into("<I", pkt, 0x0C, receiver_id)
    struct.pack_into(">I", pkt, 0x10, 0x70)  # payload size
    pkt[0x14] = 0x01  # requestreply
    pkt[0x15] = 0x00  # requesttype = login
    struct.pack_into(">H", pkt, 0x16, auth_seq)
    struct.pack_into("<H", pkt, 0x1A, tok_request)

    enc_user = _encode_credentials(username)
    enc_pass = _encode_credentials(password)
    comp = computer_name.encode("ascii")[:16]

    pkt[0x40 : 0x40 + len(enc_user)] = enc_user
    pkt[0x50 : 0x50 + len(enc_pass)] = enc_pass
    pkt[0x60 : 0x60 + len(comp)] = comp
    return bytes(pkt)


def _build_conninfo_packet(
    sender_id: int,
    receiver_id: int,
    username: str,
    token: int,
    tok_request: int,
    auth_seq: int = 0,
    civ_local_port: int = 0,
) -> bytes:
    pkt = bytearray(0x90)
    struct.pack_into("<I", pkt, 0x00, 0x90)
    struct.pack_into("<H", pkt, 0x04, PTYPE_DATA)
    struct.pack_into("<I", pkt, 0x08, sender_id)
    struct.pack_into("<I", pkt, 0x0C, receiver_id)
    struct.pack_into(">I", pkt, 0x10, 0x80)
    pkt[0x14] = 0x01
    pkt[0x15] = 0x03  # conninfo
    struct.pack_into(">H", pkt, 0x16, auth_seq)
    struct.pack_into("<H", pkt, 0x1A, tok_request)
    struct.pack_into("<I", pkt, 0x1C, token)

    struct.pack_into("<H", pkt, 0x27, 0x8010)
    name = b"IC-9700"
    pkt[0x40 : 0x40 + len(name)] = name

    enc_user = _encode_credentials(username)
    pkt[0x60 : 0x60 + len(enc_user)] = enc_user

    pkt[0x70] = 0x00  # rx disable (prevent audio flood on CI-V port)
    pkt[0x71] = 0x00  # tx disable
    pkt[0x72] = 0x00  # rx codec none
    pkt[0x73] = 0x00  # tx codec none
    struct.pack_into(">I", pkt, 0x74, 0)
    struct.pack_into(">I", pkt, 0x78, 0)
    struct.pack_into(">I", pkt, 0x7C, civ_local_port)
    struct.pack_into(">I", pkt, 0x80, 0)
    struct.pack_into(">I", pkt, 0x84, 150)
    pkt[0x88] = 0x01
    return bytes(pkt)


def _build_token_ack(
    sender_id: int, receiver_id: int, token: int, tok_request: int
) -> bytes:
    pkt = bytearray(0x40)
    struct.pack_into("<I", pkt, 0x00, 0x40)
    struct.pack_into("<H", pkt, 0x04, PTYPE_DATA)
    struct.pack_into("<I", pkt, 0x08, sender_id)
    struct.pack_into("<I", pkt, 0x0C, receiver_id)
    struct.pack_into(">I", pkt, 0x10, 0x30)
    pkt[0x14] = 0x01
    pkt[0x15] = 0x02  # token ack
    struct.pack_into("<I", pkt, 0x1C, token)
    struct.pack_into("<H", pkt, 0x1A, tok_request)
    return bytes(pkt)


# ------------------------------------------------------------------
# Async UDP transport (built on std socket + recv thread)
# ------------------------------------------------------------------
class _AsyncIcomTransport:
    """Internal asyncio-compatible UDP transport for one Icom LAN port.

    Uses a standard blocking socket for I/O and a background thread for
    reading.  This avoids Windows ProactorEventLoop datagram-endpoint
    issues when the transport runs in a background thread.
    """

    def __init__(self, discard_data: bool = False):
        self.my_id: int = 0
        self.remote_id: int = 0
        self.send_seq: int = 0
        self.ping_seq: int = 0
        self._sock: Optional[socket.socket] = None
        self._addr: Optional[tuple[str, int]] = None
        self._rx_queue: asyncio.Queue = asyncio.Queue(maxsize=8192)
        self._running: bool = False
        self._tasks: list[asyncio.Task] = []
        self._tx_buffer: OrderedDict[int, bytes] = OrderedDict()
        self._last_tracked_send: float = 0.0
        self._discard_data = discard_data
        self._recv_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # -- public async API --

    async def connect(
        self, host: str, port: int, local_port: int = 0, sock: Optional[socket.socket] = None
    ) -> None:
        self._loop = asyncio.get_running_loop()
        if sock is not None:
            self._sock = sock
            self._sock.connect((host, port))
            self._sock.setblocking(True)
        else:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Always bind first so the kernel chooses a local port we can report.
            self._sock.bind(("0.0.0.0", local_port or 0))
            self._sock.connect((host, port))
            self._sock.setblocking(True)

        laddr = self._sock.getsockname()
        self.my_id = (laddr[1] & 0xFFFF) | 0x10000
        self._addr = (host, port)
        logger.info("UDP socket to %s:%d my_id=0x%08X", host, port, self.my_id)
        # Start recv thread immediately so discover/handshake can receive.
        self._running = True
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

    async def discover(self, retries: int = 10, timeout: float = 1.0) -> None:
        for attempt in range(retries):
            self._send_control(PTYPE_ARE_YOU_THERE, 0)
            try:
                data = await asyncio.wait_for(self._rx_queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.debug("AreYouThere attempt %d/%d", attempt + 1, retries)
                continue
            if len(data) >= HEADER_SIZE:
                ptype = struct.unpack_from("<H", data, 4)[0]
                if ptype == PTYPE_I_AM_HERE:
                    self.remote_id = struct.unpack_from("<I", data, 8)[0]
                    logger.info("IAmHere remote_id=0x%08X", self.remote_id)
                    return
        raise TimeoutError("Radio did not respond to discovery")

    async def ready_handshake(self, timeout: float = 1.0) -> None:
        self._send_control(PTYPE_ARE_YOU_READY, 0)
        for _ in range(5):
            try:
                data = await asyncio.wait_for(self._rx_queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                break
            if len(data) >= HEADER_SIZE:
                ptype = struct.unpack_from("<H", data, 4)[0]
                if ptype == PTYPE_ARE_YOU_READY:
                    logger.info("IAmReady received")
                    return
        logger.warning("No explicit IAmReady, proceeding anyway")

    def start_loops(self) -> None:
        """Start background keepalive tasks (ping + idle).

        The receive thread is already started by connect().
        """
        self._tasks.append(asyncio.create_task(self._ping_loop()))
        self._tasks.append(asyncio.create_task(self._idle_loop()))

    async def send_tracked(self, data: bytes) -> None:
        seq = self.send_seq
        self.send_seq = (self.send_seq + 1) & 0xFFFF
        # Mirror wfview: clear TX buffer on sequence rollover
        if seq == 0:
            self._tx_buffer.clear()
        pkt = bytearray(data)
        struct.pack_into("<H", pkt, 6, seq)
        pkt = bytes(pkt)
        self._tx_buffer[seq] = pkt
        if len(self._tx_buffer) > 500:
            self._tx_buffer.popitem(last=False)
        self._raw_send(pkt)
        self._last_tracked_send = time.monotonic()

    async def recv_packet(self, timeout: float = 5.0) -> bytes:
        return await asyncio.wait_for(self._rx_queue.get(), timeout=timeout)

    async def disconnect(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        if self.remote_id:
            self._send_control(PTYPE_DISCONNECT, 0)
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        if self._recv_thread is not None:
            self._recv_thread.join(timeout=1.0)
            self._recv_thread = None

    # -- internal helpers --

    def _raw_send(self, data: bytes) -> None:
        if self._sock is not None:
            try:
                # Use send() for connected UDP socket; sendall() works on
                # POSIX but can misbehave on Windows with UDP.
                self._sock.send(data)
            except OSError as exc:
                logger.warning("UDP send failed: %s", exc)

    def _send_control(self, ptype: int, seq: int) -> None:
        self._raw_send(_build_control(self.my_id, self.remote_id, ptype, seq))

    def _recv_loop(self) -> None:
        """Background thread: read from socket and push to asyncio queue."""
        if self._sock is None or self._loop is None:
            return
        self._sock.settimeout(0.5)
        while self._running:
            try:
                data = self._sock.recv(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                continue
            self._on_datagram(data)

    def _on_datagram(self, data: bytes) -> None:
        if len(data) < HEADER_SIZE:
            return
        ptype = struct.unpack_from("<H", data, 4)[0]
        seq = struct.unpack_from("<H", data, 6)[0]
        sender_id = struct.unpack_from("<I", data, 8)[0]

        # Ping
        if ptype == PTYPE_PING and len(data) == PING_SIZE:
            if data[0x10] == 0x00:
                reply = bytearray(PING_SIZE)
                struct.pack_into("<I", reply, 0, PING_SIZE)
                struct.pack_into("<H", reply, 4, PTYPE_PING)
                struct.pack_into("<H", reply, 6, seq)
                struct.pack_into("<I", reply, 8, self.my_id)
                struct.pack_into("<I", reply, 0x0C, self.remote_id)
                reply[0x10] = 0x01
                reply[0x11:0x15] = data[0x11:0x15]
                self._raw_send(bytes(reply))
            return

        # Retransmit request
        if ptype == PTYPE_CONTROL and len(data) == CONTROL_SIZE:
            if seq in self._tx_buffer:
                self._raw_send(self._tx_buffer[seq])
            return

        if self._discard_data and ptype == PTYPE_DATA:
            return

        # Filter spectrum/scope data (cmd 0x27) BEFORE queueing
        # Spectrum packet: 16B UDP hdr + 5B CI-V hdr (C1+datalen+seq) + CI-V frame
        # CMD byte is at offset HEADER_SIZE + 5 + 4 = 25
        if ptype == PTYPE_DATA and len(data) >= HEADER_SIZE + 10:
            off = HEADER_SIZE + 5  # CI-V frame start after C1 header
            if off + 5 <= len(data) and data[off] == 0xFE and data[off + 1] == 0xFE:
                if data[off + 4] == 0x27:  # spectrum command
                    return

        if self.remote_id == 0 and sender_id != 0:
            self.remote_id = sender_id

        # Log non-control DATA packets (CI-V responses) — summary only
        if ptype == PTYPE_DATA and len(data) > CONTROL_SIZE:
            payload_len = len(data) - HEADER_SIZE
            logger.debug(
                "CI-V RX: len=%d ptype=0x%02X sender=0x%08X payload_len=%d",
                len(data), ptype, sender_id, payload_len,
            )

        try:
            # Safe cross-thread queue push via call_soon_threadsafe
            self._loop.call_soon_threadsafe(self._rx_queue.put_nowait, data)
        except Exception:
            pass

    async def _ping_loop(self) -> None:
        try:
            while self._running:
                self._raw_send(_build_ping(self.my_id, self.remote_id, self.ping_seq))
                self.ping_seq = (self.ping_seq + 1) & 0xFFFF
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    async def _idle_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(0.1)
                if time.monotonic() - self._last_tracked_send >= 0.1:
                    await self.send_tracked(
                        _build_control(self.my_id, self.remote_id, PTYPE_DATA, 0)
                    )
        except asyncio.CancelledError:
            pass


# ------------------------------------------------------------------
# Synchronous wrapper exposing CIVSerial-like interface
# ------------------------------------------------------------------
class LanCIVTransport:
    """
    LAN transport that mimics CIVSerial's synchronous interface.

    Internally runs an asyncio event loop in a background thread.
    """

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._control: Optional[_AsyncIcomTransport] = None
        self._civ: Optional[_AsyncIcomTransport] = None
        self._callback: Optional[Callable] = None
        self._running = False
        self._token = 0
        self._tok_request = 0
        self._host = ""
        self._civ_read_task: Optional[asyncio.Task] = None
        self._civ_seq: int = 0

    # -- sync API matching CIVSerial --

    def is_open(self) -> bool:
        return self._running and self._civ is not None

    def set_callback(self, callback: Callable) -> None:
        self._callback = callback

    def send_raw(self, data: bytes) -> bool:
        if not self._civ or not self._running:
            return False
        # Wrap CI-V frame in UDP DATA packet with CI-V data header
        # Format: [16-byte UDP hdr] [C1] [2-byte LE datalen] [2-byte BE seq] [CI-V frame]
        self._civ_seq = (self._civ_seq + 1) & 0xFFFF
        total = HEADER_SIZE + 5 + len(data)
        pkt = bytearray(total)
        struct.pack_into("<I", pkt, 0, total)
        struct.pack_into("<H", pkt, 4, PTYPE_DATA)
        sender_id = self._civ.my_id
        struct.pack_into("<I", pkt, 8, sender_id)
        struct.pack_into("<I", pkt, 0x0C, self._civ.remote_id)
        pkt[HEADER_SIZE] = 0xC1
        struct.pack_into("<H", pkt, HEADER_SIZE + 1, len(data))
        struct.pack_into(">H", pkt, HEADER_SIZE + 3, self._civ_seq)
        pkt[HEADER_SIZE + 5 :] = data
        print(f"[LAN] CI-V TX: sender=0x{sender_id:08X} receiver=0x{self._civ.remote_id:08X} payload={data.hex()}", flush=True)
        logger.info(
            "CI-V TX: sender=0x%08X receiver=0x%08X payload=%s",
            sender_id, self._civ.remote_id, data.hex(),
        )
        fut = asyncio.run_coroutine_threadsafe(
            self._civ.send_tracked(bytes(pkt)), self._loop
        )
        try:
            fut.result(timeout=2.0)
            return True
        except Exception as e:
            logger.warning("send_raw failed: %s", e)
            print(f"[LAN] send_raw failed: {e}", flush=True)
            return False

    def send(self, cmd: int, subcmd: int = None, data: bytes = None) -> bool:
        from civ import build_command

        # Use standard CI-V addresses (parse_response also accepts 0x00)
        frame = build_command(cmd, subcmd, data)
        return self.send_raw(frame)

    def open(
        self,
        host: str,
        username: str = "",
        password: str = "",
        control_port: int = 50001,
        civ_port: int = 50002,
    ) -> bool:
        self.close()
        self._host = host
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

        try:
            asyncio.run_coroutine_threadsafe(
                self._async_open(host, username, password, control_port, civ_port),
                self._loop,
            ).result(timeout=15.0)
            return True
        except Exception as e:
            logger.error("LAN open failed: %s", e)
            self.close()
            raise

    def close(self) -> None:
        self._running = False
        if self._civ_read_task and self._loop and not self._civ_read_task.done():
            try:
                self._loop.call_soon_threadsafe(self._civ_read_task.cancel)
            except Exception:
                pass
        if self._loop and self._loop.is_running():
            if self._control:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._control.disconnect(), self._loop
                    ).result(timeout=2.0)
                except Exception:
                    pass
            if self._civ:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._civ.disconnect(), self._loop
                    ).result(timeout=2.0)
                except Exception:
                    pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=2.0)
        self._civ_read_task = None
        self._control = None
        self._civ = None
        self._loop = None
        self._thread = None

    # -- internal async logic --

    async def _async_open(
        self, host: str, username: str, password: str, control_port: int, civ_port: int
    ) -> None:
        # 1. Control port: discovery + auth
        self._control = _AsyncIcomTransport(discard_data=False)
        await self._control.connect(host, control_port)
        await self._control.discover()
        await self._control.ready_handshake()

        # 2. Login
        self._tok_request = int(time.time()) & 0xFFFF
        login_pkt = _build_login_packet(
            username,
            password,
            sender_id=self._control.my_id,
            receiver_id=self._control.remote_id,
            tok_request=self._tok_request,
        )
        await self._control.send_tracked(login_pkt)

        # Wait for login response (0x60 bytes)
        while True:
            data = await self._control.recv_packet(timeout=5.0)
            if len(data) >= 0x60:
                error = struct.unpack_from("<I", data, 0x30)[0]
                self._token = struct.unpack_from("<I", data, 0x1C)[0]
                tok_req = struct.unpack_from("<H", data, 0x1A)[0]
                if tok_req == self._tok_request:
                    if error == 0xFFFFFFFF or error == 0xFEFFFFFF:
                        raise ConnectionError(
                            f"Login rejected: error=0x{error:08X}"
                        )
                    logger.info("Login OK token=0x%08X", self._token)
                    break

        # 3. Pre-bind CI-V socket so we can report its local port in conninfo
        civ_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        civ_sock.bind(("0.0.0.0", 0))
        civ_local_port = civ_sock.getsockname()[1]

        # 4. ConnInfo (with actual CI-V local port)
        conninfo = _build_conninfo_packet(
            sender_id=self._control.my_id,
            receiver_id=self._control.remote_id,
            username=username,
            token=self._token,
            tok_request=self._tok_request,
            civ_local_port=civ_local_port,
        )
        await self._control.send_tracked(conninfo)

        # Wait for status response (0x50 bytes)
        while True:
            data = await self._control.recv_packet(timeout=5.0)
            if len(data) >= 0x50:
                error = struct.unpack_from("<I", data, 0x30)[0]
                if error == 0xFFFFFFFF:
                    raise ConnectionError("ConnInfo rejected")
                logger.info("ConnInfo accepted")
                break

        # 5. Token ACK
        ack = _build_token_ack(
            sender_id=self._control.my_id,
            receiver_id=self._control.remote_id,
            token=self._token,
            tok_request=self._tok_request,
        )
        self._control._raw_send(ack)

        # 6. Start control keepalive
        self._control.start_loops()

        # 7. CI-V port: discovery using the pre-bound socket
        self._civ = _AsyncIcomTransport(discard_data=False)
        await self._civ.connect(host, civ_port, sock=civ_sock)
        await self._civ.discover()
        await self._civ.ready_handshake()
        # Note: CI-V port must use its own discovered IDs, NOT control port IDs.
        # Sending to control port's remote_id causes radio to ignore CI-V commands.

        # 8. Set running flag before starting read loop so it doesn't exit immediately
        self._running = True
        self._civ_read_task = asyncio.create_task(self._civ_read_loop())

    async def _civ_read_loop(self) -> None:
        buffer = bytearray()
        timeout_count = 0
        _spectrum_skipped = 0
        print(f"[LAN] CI-V read loop started on {self._host}", flush=True)
        logger.info("CI-V read loop started on port %s", self._host)
        while self._running:
            try:
                pkt = await asyncio.wait_for(self._civ.recv_packet(), timeout=1.0)
                timeout_count = 0
            except asyncio.TimeoutError:
                timeout_count += 1
                if timeout_count % 10 == 0:
                    msg = f"CI-V recv timeout x{timeout_count} (no data from radio)"
                    print(f"[LAN] {msg}", flush=True)
                    logger.info(msg)
                continue
            except Exception as exc:
                msg = f"CI-V read loop exception: {exc}"
                print(f"[LAN] {msg}", flush=True)
                logger.error(msg)
                break

            if len(pkt) <= HEADER_SIZE:
                continue
            civ_data = pkt[HEADER_SIZE:]
            buffer.extend(civ_data)
            while True:
                start = buffer.find(PREAMBLE)
                if start == -1:
                    buffer.clear()
                    break
                if start > 0:
                    buffer = buffer[start:]
                end = buffer.find(END_CODE, 2)
                if end == -1:
                    break
                frame = bytes(buffer[: end + 1])
                buffer = buffer[end + 1 :]
                # Spectrum/scope data (cmd 0x27) — silently discard
                if len(frame) >= 5 and frame[4] == 0x27:
                    _spectrum_skipped += 1
                    if _spectrum_skipped % 100 == 1:
                        logger.debug("Spectrum frames skipped: %d", _spectrum_skipped)
                    continue
                logger.info("CI-V frame: %s", frame.hex())
                self._handle_frame(frame)

    def _handle_frame(self, frame: bytes) -> None:
        from civ import (
            parse_response,
            PREAMBLE,
            CONTROLLER_ADDR,
            RADIO_ADDR,
            OK_CODE,
            NG_CODE,
        )

        if len(frame) >= 5 and frame[4] == 0x27:
            return

        parsed = parse_response(frame)
        if parsed is not None:
            logger.debug("CI-V frame parsed: cmd=0x%02X payload=%s", parsed["cmd"], parsed["payload"].hex())
            if self._callback:
                self._callback({"type": "data", **parsed})
            return

        # OK / NG (accept broadcast 0x00 in LAN mode)
        if len(frame) >= 6 and frame[0:2] == PREAMBLE:
            to_addr = frame[2]
            from_addr = frame[3]
            if to_addr in (CONTROLLER_ADDR, 0x00) and from_addr in (RADIO_ADDR, 0x00):
                if frame[4] == OK_CODE and self._callback:
                    self._callback({"type": "ok"})
                elif frame[4] == NG_CODE and self._callback:
                    self._callback({"type": "ng"})
