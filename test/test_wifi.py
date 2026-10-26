#!/usr/bin/env python3
"""
Tests for wifi.py: parsing nmcli's terse output, the background Job, the
error messages, and the fake backend the desktop uses.

Run from the project root:
    python test/run_tests.py test_wifi
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import wifi

LIST = """\
*:Colorimeter Lab:72:WPA2
 :Guest:55:
 :Home\\:Net:30:WPA1 WPA2
 :Guest:61:
 ::80:WPA2
 :Cafe:22:--
"""

DEVICE = """\
GENERAL.STATE:100 (connected)
GENERAL.CONNECTION:preconfigured
GENERAL.HWADDR:DC\\:A6\\:32\\:01\\:02\\:03
IP4.ADDRESS[1]:192.168.1.23/24
IP4.ADDRESS[2]:10.0.0.5/8
"""


def finish(job, limit_s=10.0):
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        result = job.poll()
        if result is not None:
            return result
        time.sleep(0.01)
    raise AssertionError("job did not finish")


class TestParsing(unittest.TestCase):

    def test_split_terse_unescapes(self):
        self.assertEqual(wifi.split_terse("a\\:b:c\\\\d:"), ["a:b", "c\\d", ""])

    def test_networks(self):
        nets = wifi.parse_networks(LIST)
        self.assertEqual([n.ssid for n in nets],
                         ["Colorimeter Lab", "Guest", "Home:Net", "Cafe"])
        lab, guest, home, cafe = nets
        self.assertTrue(lab.in_use and lab.secured)
        self.assertEqual((guest.signal, guest.secured), (61, False))   # strongest kept
        self.assertTrue(home.secured)
        self.assertFalse(cafe.secured)                                 # '--' is open

    def test_saved_keeps_only_wifi(self):
        text = "preconfigured:802-11-wireless\nWired connection 1:802-3-ethernet\n" \
               "Home\\:Net:802-11-wireless\n"
        self.assertEqual(wifi.parse_saved(text), ["preconfigured", "Home:Net"])

    def test_device(self):
        d = wifi.parse_device(DEVICE)
        self.assertEqual(d, wifi.Device("connected", "preconfigured",
                                        "DC:A6:32:01:02:03",
                                        ("192.168.1.23", "10.0.0.5")))

    def test_device_disconnected(self):
        d = wifi.parse_device("GENERAL.STATE:30 (disconnected)\nGENERAL.CONNECTION:--\n")
        self.assertEqual((d.state, d.connection, d.addresses), ("disconnected", "", ()))

    def test_radio(self):
        self.assertTrue(wifi.parse_radio("enabled\n"))
        self.assertFalse(wifi.parse_radio("disabled\n"))


class TestMessages(unittest.TestCase):

    def test_friendly(self):
        f = wifi.friendly
        self.assertEqual(f("sudo: a password is required"), wifi.NOT_PERMITTED)
        self.assertEqual(f("Error: Connection activation failed: Secrets were "
                           "required, but not provided."), "Wrong password?")
        self.assertEqual(f("Error: No network with SSID 'X' found."), "Not in range")
        self.assertEqual(f("Error: something odd"), "something odd")

    def test_password_is_masked(self):
        cmd = wifi.NmcliBackend().connect("Lab", "hunter2")._cmds[0]
        self.assertIn("hunter2", cmd)
        self.assertNotIn("hunter2", wifi.masked(cmd))
        self.assertIn("password ***", wifi.masked(cmd))


class TestJob(unittest.TestCase):

    def test_commands_run_in_turn(self):
        r = finish(wifi.Job([["echo", "one"], ["echo", "two"]], 5))
        self.assertTrue(r.ok)
        self.assertEqual(r.outputs, ["one\n", "two\n"])

    def test_first_failure_ends_the_job(self):
        r = finish(wifi.Job([["sh", "-c", "echo 'Error: No network with SSID x' >&2; "
                                          "exit 10"], ["echo", "never"]], 5))
        self.assertEqual((r.ok, r.message, r.outputs), (False, "Not in range", []))

    def test_a_missing_program(self):
        r = finish(wifi.Job([["no-such-program-xyz"]], 5))
        self.assertFalse(r.ok)
        self.assertIn("not found", r.message)

    def test_timeout_kills(self):
        r = finish(wifi.Job([["sleep", "5"]], 0.2))
        self.assertEqual((r.ok, r.message), (False, "No answer from the network"))

    def test_cancel(self):
        job = wifi.Job([["sleep", "5"]], 10)
        self.assertIsNone(job.poll())
        job.cancel()
        self.assertEqual(job.poll().message, "Cancelled")


class TestFakeBackend(unittest.TestCase):

    def setUp(self):
        self.b = wifi.FakeBackend()

    def test_status_parses(self):
        radio, dev, lst = finish(self.b.status()).outputs
        self.assertTrue(wifi.parse_radio(radio))
        self.assertEqual(wifi.parse_device(dev).addresses, ("192.168.1.23",))
        self.assertIn("Home:Net", [n.ssid for n in wifi.parse_networks(lst)])

    def test_secured_needs_the_password_until_saved(self):
        self.assertEqual(finish(self.b.connect("Phone hotspot")).message,
                         "Wrong password?")
        self.assertTrue(finish(self.b.connect("Phone hotspot",
                                              wifi.FakeBackend.PASSWORD)).ok)
        self.assertIn("Phone hotspot", self.b.saved)
        self.assertTrue(finish(self.b.connect("Phone hotspot")).ok)

    def test_forget(self):
        finish(self.b.forget("Colorimeter Lab"))
        self.assertNotIn("Colorimeter Lab", self.b.saved)
        self.assertIsNone(self.b.connected)


if __name__ == "__main__":
    unittest.main()
