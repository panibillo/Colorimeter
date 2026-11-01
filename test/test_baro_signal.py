#!/usr/bin/env python3
"""
Unit tests for baro_signal (src/baro_signal.py).

TOOL-DEVELOPMENT CODE: delete this file along with src/baro_signal.py (see
that module's "Removing it" section).

A UDP listener on 127.0.0.1 stands in for the laptop's baro_live.py.  It can
answer each line or stay silent, so both the normal exchange and the resends
are checked without a network.  A made-up secrets_net.py in a temporary
directory points at it; the real config/secrets_net.py is never read.

Run from the project root:
    python test/run_tests.py test_baro_signal
"""

import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import baro_signal


class Listener:
    """A stand-in for the laptop: records each line, answers if told to."""

    def __init__(self, answer: bool):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]
        self.answer = answer
        self.lines = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                data, sender = self.sock.recvfrom(1024)
            except socket.timeout:
                continue
            line = data.decode("ascii")
            self.lines.append(line)
            if self.answer:
                self.sock.sendto(f"OK\t{line.split(chr(9))[0]}".encode(), sender)

    def wait_for(self, n: int, timeout: float = 3.0) -> list:
        """The lines received, once there are n of them (or the time is up)."""
        end = time.monotonic() + timeout
        while len(self.lines) < n and time.monotonic() < end:
            time.sleep(0.01)
        return list(self.lines)

    def close(self):
        self._stop.set()
        self._thread.join(1.0)
        self.sock.close()


class TestBaroSignal(unittest.TestCase):

    def setUp(self):
        # Each test starts switched off.  A worker thread left by an earlier
        # test stays blocked on its own, now unreachable, queue.
        baro_signal._queue = None
        self._wait, self._resends = baro_signal.RESEND_WAIT_S, baro_signal.RESENDS
        self.tmp = tempfile.TemporaryDirectory()
        self.listener = None

    def tearDown(self):
        baro_signal._queue = None
        baro_signal.RESEND_WAIT_S, baro_signal.RESENDS = self._wait, self._resends
        if self.listener is not None:
            self.listener.close()
        self.tmp.cleanup()

    def _config(self, text: str) -> Path:
        path = Path(self.tmp.name) / "baro_udp.json"
        path.write_text(text)
        return path

    def _secrets(self, port: int, label: str = "Home Net ") -> Path:
        """A stand-in secrets_net.py.  The label is spelt unlike wifidict's on purpose."""
        path = Path(self.tmp.name) / "secrets_net.py"
        path.write_text(
            "wifidict = {'home net': ('TestSSID', 'not-a-password'),\n"
            "            'elsewhere': ('OtherSSID', 'x')}\n"
            f"udp_dict = {{({label!r}, 'lenore'): ('127.0.0.1', {port}),\n"
            "            ('elsewhere', 'lenore'): ('127.0.0.2', 1)}\n"
            "local_udp_piezo_port = 0\n")
        return path

    def _enable_to(self, listener: Listener, cfg=None) -> None:
        self.listener = listener
        cfg = cfg or {"target": "lenore", "network": "home net"}
        self.assertTrue(baro_signal.enable(self._config(json.dumps(cfg)),
                                           self._secrets(listener.port)))

    def test_parts_are_tab_joined_with_samp_no_first(self):
        self._enable_to(Listener(answer=True))
        baro_signal.send("NEWRUN", "ZZ1001")
        baro_signal.send("START", "ZZ1001", "Reagent2")
        baro_signal.send("MARK", "ZZ1001", "EndReagent2")
        baro_signal.send("STOP", "ZZ1001")
        self.assertEqual(self.listener.wait_for(4), [
            "NEWRUN\tZZ1001",
            "START\tZZ1001\tReagent2",
            "MARK\tZZ1001\tEndReagent2",
            "STOP\tZZ1001",
        ])

    def test_a_tab_or_line_break_inside_a_part_becomes_a_space(self):
        self._enable_to(Listener(answer=True))
        baro_signal.send("MARK", "ZZ\t1001", "End\nReagent2")
        self.assertEqual(self.listener.wait_for(1),
                         ["MARK\tZZ 1001\tEnd Reagent2"])

    def test_an_answered_line_is_sent_once(self):
        self._enable_to(Listener(answer=True))
        baro_signal.send("STOP", "ZZ1001")
        self.listener.wait_for(1)
        time.sleep(0.3)
        self.assertEqual(self.listener.lines, ["STOP\tZZ1001"])

    def test_no_answer_means_resends_then_giving_up(self):
        baro_signal.RESEND_WAIT_S = 0.05
        baro_signal.RESENDS = 5
        self._enable_to(Listener(answer=False))
        baro_signal.send("STOP", "ZZ1001")
        self.assertEqual(self.listener.wait_for(6), ["STOP\tZZ1001"] * 6)
        time.sleep(0.3)                  # no seventh try
        self.assertEqual(len(self.listener.lines), 6)

    def test_the_connected_ssid_picks_the_entry(self):
        with mock.patch.object(baro_signal, "_connected_ssid",
                               return_value="TestSSID"):
            self._enable_to(Listener(answer=True), cfg={"target": "lenore"})
            baro_signal.send("STOP", "ZZ1001")
            self.assertEqual(self.listener.wait_for(1), ["STOP\tZZ1001"])

    def test_off_wifi_nothing_is_sent(self):
        with mock.patch.object(baro_signal, "_connected_ssid", return_value=None):
            self._enable_to(Listener(answer=True), cfg={"target": "lenore"})
            with self.assertLogs("baro_signal", "WARNING") as logs:
                baro_signal.send("STOP", "ZZ1001")
                time.sleep(0.3)
        self.assertEqual(self.listener.lines, [])
        self.assertIn("not on Wi-Fi", logs.output[0])

    def test_no_secret_reaches_the_log(self):
        with self.assertLogs("baro_signal", "INFO") as logs:
            self._enable_to(Listener(answer=True))
            baro_signal.send("STOP", "ZZ1001")
            self.listener.wait_for(1)
            time.sleep(0.2)
        text = "\n".join(logs.output)
        self.assertIn("STOP\\tZZ1001 -> OK\\tSTOP", text)
        for secret in ("127.0.0.1", str(self.listener.port), "TestSSID",
                       "not-a-password"):
            self.assertNotIn(secret, text)

    def test_send_does_nothing_until_enabled(self):
        self.listener = Listener(answer=True)
        baro_signal.send("STOP", "ZZ1001")      # must not raise either
        time.sleep(0.2)
        self.assertEqual(self.listener.lines, [])

    def test_a_missing_file_leaves_it_off(self):
        absent = Path(self.tmp.name) / "absent"
        good = self._config('{"target": "lenore"}')
        for cfg, secrets in ((absent, self._secrets(1)), (good, absent)):
            with self.subTest(cfg=cfg.name, secrets=secrets.name):
                self.assertFalse(baro_signal.enable(cfg, secrets))
                self.assertIsNone(baro_signal._queue)

    def test_a_bad_file_leaves_it_off(self):
        secrets = self._secrets(1)
        for text in ("not json", '{"network": "x"}', '["lenore"]',
                     '{"target": "nobody"}'):
            with self.subTest(text=text):
                self.assertFalse(baro_signal.enable(self._config(text), secrets))
                self.assertIsNone(baro_signal._queue)

    def test_a_broken_secrets_file_is_not_quoted(self):
        bad = Path(self.tmp.name) / "secrets_net.py"
        bad.write_text("wifidict = {'home': ('TestSSID', 'not-a-password')\n")
        with self.assertLogs("baro_signal", "WARNING") as logs:
            self.assertFalse(baro_signal.enable(
                self._config('{"target": "lenore"}'), bad))
        self.assertNotIn("not-a-password", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
