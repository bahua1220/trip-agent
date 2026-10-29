"""Ensure offline runners fail even if production catches a network exception."""

import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from offline_network import OfflineNetworkGuard


class OfflineNetworkTests(unittest.TestCase):
    def test_remote_dns_and_both_connect_variants_are_blocked_and_recorded(self):
        with OfflineNetworkGuard() as guard, socket.socket() as sock:
            for action in (lambda: socket.getaddrinfo("example.test", 443),
                           lambda: sock.connect(("203.0.113.1", 443)),
                           lambda: sock.connect_ex(("203.0.113.1", 443))):
                with self.assertRaisesRegex(AssertionError, "离线回归禁止外网"):
                    action()
        self.assertEqual(3, len(guard.attempts))
        self.assertEqual(["dns", "connect", "connect_ex"], [item["operation"] for item in guard.attempts])

    def test_loopback_delegates_and_hooks_are_restored(self):
        original = socket.socket.connect
        with patch.object(socket.socket, "connect", return_value=None) as connect, patch.object(socket, "getaddrinfo", return_value=[]) as lookup:
            with OfflineNetworkGuard() as guard, socket.socket() as sock:
                sock.connect(("127.0.0.1", 12345))
                self.assertEqual([], socket.getaddrinfo("localhost", 12345))
            self.assertFalse(guard.attempts)
            connect.assert_called_once()
            lookup.assert_called_once()
        self.assertIs(original, socket.socket.connect)

    def test_swallowed_network_exception_is_still_recorded(self):
        with OfflineNetworkGuard() as guard:
            try:
                socket.getaddrinfo("api.tavily.com", 443)
            except Exception:
                pass
        self.assertEqual(1, len(guard.attempts))


if __name__ == "__main__":
    unittest.main()
