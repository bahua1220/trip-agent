"""Process-wide offline test guard: allow local ASGI socket pairs, deny egress."""

import ipaddress
import socket
from contextlib import ExitStack
from unittest.mock import patch


class OfflineNetworkGuard:
    def __init__(self):
        self.attempts = []
        self.stack = ExitStack()

    @staticmethod
    def local(host):
        if isinstance(host, bytes):
            host = host.decode("ascii", errors="replace")
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(str(host).split("%", 1)[0]).is_loopback
        except ValueError:
            return False

    def deny(self, operation, host):
        self.attempts.append({"operation": operation, "host": str(host)})
        raise AssertionError(f"离线回归禁止外网：{operation} {host}")

    def __enter__(self):
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex
        original_lookup = socket.getaddrinfo
        guard = self

        def connect(sock, address):
            if not isinstance(address, tuple) or not guard.local(address[0]):
                guard.deny("connect", address[0] if isinstance(address, tuple) else address)
            return original_connect(sock, address)

        def connect_ex(sock, address):
            if not isinstance(address, tuple) or not guard.local(address[0]):
                guard.deny("connect_ex", address[0] if isinstance(address, tuple) else address)
            return original_connect_ex(sock, address)

        def lookup(host, *args, **kwargs):
            if host is not None and not guard.local(host):
                guard.deny("dns", host)
            return original_lookup(host, *args, **kwargs)

        self.stack.enter_context(patch.object(socket.socket, "connect", connect))
        self.stack.enter_context(patch.object(socket.socket, "connect_ex", connect_ex))
        self.stack.enter_context(patch.object(socket, "getaddrinfo", lookup))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)
