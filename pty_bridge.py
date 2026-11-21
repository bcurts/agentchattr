"""Optional authenticated local bridge used when termgrid hosts the console."""
import json
import secrets
import socket
import struct
import threading
import time

MAX_FRAME = 1024 * 1024


def _recv_exact(sock, size):
    result = bytearray()
    while len(result) < size:
        data = sock.recv(size - len(result))
        if not data:
            raise ConnectionError('bridge connection closed')
        result.extend(data)
    return bytes(result)


def send_frame(sock, value):
    data = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(data) > MAX_FRAME:
        raise ValueError('bridge frame exceeds limit')
    sock.sendall(struct.pack('!I', len(data)) + data)


def receive_frame(sock):
    length = struct.unpack('!I', _recv_exact(sock, 4))[0]
    if length > MAX_FRAME:
        raise ValueError('bridge frame exceeds limit')
    result = json.loads(_recv_exact(sock, length))
    if not isinstance(result, dict):
        raise ValueError('bridge frame must be an object')
    return result


class BridgeClient:
    def __init__(self, port, token, retry_timeout=60):
        self.port, self.token = port, token
        self.retry_timeout = retry_timeout
        self.lock = threading.Lock()
        self.socket = None
        self._connect()

    def _connect(self, timeout=5):
        sock = socket.create_connection(('127.0.0.1', self.port), timeout=timeout)
        try:
            send_frame(sock, {'type': 'hello', 'token': self.token})
            if receive_frame(sock) != {'type': 'ready'}:
                raise ConnectionError('bridge authentication failed')
        except Exception:
            sock.close()
            raise
        self.socket = sock

    def inject(self, text):
        if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_FRAME // 2:
            raise ValueError('invalid injection text')
        request_id = secrets.token_hex(16)
        with self.lock:
            deadline = time.monotonic() + self.retry_timeout
            while time.monotonic() < deadline:
                try:
                    remaining = max(.01, min(5, deadline - time.monotonic()))
                    if self.socket is None:
                        self._connect(timeout=remaining)
                    self.socket.settimeout(remaining)
                    send_frame(self.socket, {'type': 'inject', 'id': request_id, 'text': text})
                    if receive_frame(self.socket) != {'type': 'accepted', 'id': request_id}:
                        raise ConnectionError('invalid bridge acknowledgement')
                    return
                except (OSError, ConnectionError, ValueError):
                    if self.socket:
                        self.socket.close()
                    self.socket = None
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        time.sleep(min(1, remaining))
            raise TimeoutError('PTY prompt delivery was not confirmed; check the agent before resending')

    def close(self):
        if self.socket:
            self.socket.close()
            self.socket = None
