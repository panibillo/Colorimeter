"""
wifi.py  —  Wi-Fi status and connections through NetworkManager (nmcli).
------------------------------------------------------------------------
Backs Menu → Advanced → Wifi.  No screen code lives here: this module runs
nmcli in the background and turns its output into plain values.

Why the pieces are shaped as they are:

  * Every command runs as a background Job, polled from the main loop.  A
    connect can take half a minute, and the app is a single-threaded loop that
    must keep the screen and timers alive meanwhile.
  * Commands that change anything (connect, forget, rescan, radio on) run as
    `sudo -n nmcli`, as the app already does for `sudo shutdown`.  `-n` makes a
    missing sudo rule fail at once instead of waiting for a password nobody
    can type.  Reads run as plain nmcli.
  * `nmcli device wifi connect` saves a profile with autoconnect on, in
    /etc/NetworkManager/system-connections/.  That is what makes a joined
    network come back after a reboot; nothing else is needed.
  * A password is never logged: logged commands show `password ***`.
  * Under COLORIMETER_SIMULATION=1, backend() returns FakeBackend, so
    developing on the desktop never touches the desktop's own Wi-Fi.

Public surface
--------------
    backend() -> NmcliBackend | FakeBackend
    Network, Device, Result, Job
    parse_networks(text), parse_saved(text), parse_device(text), parse_radio(text)
    hostname() -> str
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

log = logging.getLogger(__name__)

DEVICE = "wlan0"
NOT_PERMITTED = "Not permitted: the app user needs passwordless sudo"
WIFI_TYPE = "802-11-wireless"

STATUS_TIMEOUT_S = 15.0
SCAN_TIMEOUT_S = 30.0
CONNECT_TIMEOUT_S = 60.0


# ── Values ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Network:
    """One network the radio can see."""
    ssid: str
    signal: int          # 0-100
    secured: bool
    in_use: bool         # the one this device is connected to


@dataclass(frozen=True)
class Device:
    """What `nmcli device show wlan0` says about the Wi-Fi device."""
    state: str           # 'connected', 'disconnected', 'unavailable', ...
    connection: str      # the active profile's name, or ''
    mac: str
    addresses: tuple     # IPv4 addresses without the prefix length


@dataclass
class Result:
    """How a Job ended: ok, the stdout of each command, and a message if not."""
    ok: bool
    outputs: List[str] = field(default_factory=list)
    message: str = ""


# ── Parsing nmcli's terse (-t) output ──────────────────────────────────────────

def split_terse(line: str) -> List[str]:
    """
    Split one line of `nmcli -t` output into fields.

    Fields are separated by ':', and a ':' or '\\' inside a field is escaped
    with a backslash, so 'Home\\:Net:72' is ['Home:Net', '72'].
    """
    fields, cur, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    fields.append("".join(cur))
    return fields


def parse_networks(text: str) -> List[Network]:
    """
    Networks from `nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list`,
    strongest first.

    A hidden network (empty SSID) is left out, since it cannot be picked by
    name.  An SSID seen on several access points is listed once, at its
    strongest signal, marked in use if any of them is.
    """
    best: dict = {}
    for line in text.splitlines():
        parts = split_terse(line)
        if len(parts) < 4 or not parts[1]:
            continue
        in_use, ssid, signal, security = parts[0], parts[1], parts[2], parts[3]
        try:
            strength = int(signal)
        except ValueError:
            strength = 0
        net = Network(ssid, strength, security.strip() not in ("", "--"),
                      in_use.strip() == "*")
        old = best.get(ssid)
        if old is None:
            best[ssid] = net
        else:
            best[ssid] = Network(ssid, max(old.signal, net.signal),
                                 old.secured or net.secured,
                                 old.in_use or net.in_use)
    return sorted(best.values(), key=lambda n: (not n.in_use, -n.signal, n.ssid))


def parse_saved(text: str) -> List[str]:
    """Names of the saved Wi-Fi profiles, from `nmcli -t -f NAME,TYPE connection show`."""
    names = []
    for line in text.splitlines():
        parts = split_terse(line)
        if len(parts) >= 2 and parts[1] == WIFI_TYPE and parts[0]:
            names.append(parts[0])
    return names


def parse_device(text: str) -> Device:
    """
    The device's state, from `nmcli -t -f GENERAL.STATE,GENERAL.CONNECTION,
    GENERAL.HWADDR,IP4.ADDRESS device show wlan0`.

    Lines look like 'GENERAL.STATE:100 (connected)' and
    'IP4.ADDRESS[1]:192.168.1.23/24'.
    """
    state, connection, mac, addresses = "", "", "", []
    for line in text.splitlines():
        parts = split_terse(line)
        if len(parts) < 2:
            continue
        key, value = parts[0], ":".join(parts[1:])
        if key == "GENERAL.STATE":
            state = value[value.find("(") + 1:value.rfind(")")] if "(" in value else value
        elif key == "GENERAL.CONNECTION":
            connection = "" if value == "--" else value
        elif key == "GENERAL.HWADDR":
            mac = value
        elif key.startswith("IP4.ADDRESS") and value:
            addresses.append(value.split("/")[0])
    return Device(state, connection, mac, tuple(addresses))


def parse_radio(text: str) -> bool:
    """True when `nmcli -t radio wifi` says 'enabled'."""
    return text.strip() == "enabled"


def friendly(stderr: str) -> str:
    """A short message for the screen, from nmcli's or sudo's error text."""
    text = stderr.strip()
    low = text.lower()
    if "sudo:" in low or "not authorized" in low or "not allowed" in low:
        return NOT_PERMITTED
    if "secrets were required" in low or "802-1x supplicant" in low:
        return "Wrong password?"
    if "no network with ssid" in low or ("not found" in low and "ssid" in low):
        return "Not in range"
    if "unknown connection" in low:
        return "No saved network by that name"
    last = text.splitlines()[-1] if text else "nmcli failed"
    return last.removeprefix("Error: ").strip()[:80]


def masked(cmd: Sequence[str]) -> str:
    """The command as text for the log, with any password replaced by ***."""
    out, hide = [], False
    for arg in cmd:
        out.append("***" if hide else arg)
        hide = arg == "password"
    return " ".join(out)


def hostname() -> str:
    """This machine's name, as other computers on the network can find it."""
    return socket.gethostname()


# ── Background jobs ────────────────────────────────────────────────────────────

class Job:
    """
    Runs one or more commands in turn, in the background.

    poll() returns None while they run, or a Result when they end.  The first
    command that fails ends the job.  The whole job has one deadline.
    """

    def __init__(self, cmds: Sequence[Sequence[str]], timeout_s: float):
        self._cmds = [list(c) for c in cmds]
        self._deadline = time.monotonic() + timeout_s
        self._proc: Optional[subprocess.Popen] = None
        self._outputs: List[str] = []
        self.result: Optional[Result] = None

    def _start_next(self) -> None:
        cmd = self._cmds[len(self._outputs)]
        log.info("wifi: %s", masked(cmd))
        env = dict(os.environ, LC_ALL="C")       # English messages, to match on
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True, env=env)

    def _finish(self, result: Result) -> Result:
        self.result, self._proc = result, None
        if not result.ok:
            log.warning("wifi: failed: %s", result.message)
        return result

    def poll(self) -> Optional[Result]:
        if self.result is not None:
            return self.result
        try:
            if self._proc is None:
                self._start_next()
            if self._proc.poll() is None:
                if time.monotonic() > self._deadline:
                    self._proc.kill()
                    self._proc.wait()
                    return self._finish(Result(False, self._outputs,
                                               "No answer from the network"))
                return None
            out, err = self._proc.communicate()
        except FileNotFoundError as e:
            return self._finish(Result(False, self._outputs, f"{e.filename} not found"))
        if self._proc.returncode != 0:
            return self._finish(Result(False, self._outputs, friendly(err)))
        self._outputs.append(out)
        self._proc = None
        if len(self._outputs) == len(self._cmds):
            return self._finish(Result(True, self._outputs))
        return None

    def cancel(self) -> None:
        """Stop a running command.  NetworkManager may still finish what it started."""
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait()
        if self.result is None:
            self.result = Result(False, self._outputs, "Cancelled")


# ── Backends ───────────────────────────────────────────────────────────────────

NMCLI = ["nmcli"]
SUDO_NMCLI = ["sudo", "-n", "nmcli"]
_STATUS_FIELDS = "GENERAL.STATE,GENERAL.CONNECTION,GENERAL.HWADDR,IP4.ADDRESS"
_LIST_FIELDS = "IN-USE,SSID,SIGNAL,SECURITY"


class NmcliBackend:
    """The real thing.  Each method returns a Job; see the table in each docstring."""

    def status(self) -> Job:
        """Outputs: radio, device, the cached network list (for the signal)."""
        return Job([NMCLI + ["-t", "radio", "wifi"],
                    NMCLI + ["-t", "-f", _STATUS_FIELDS, "device", "show", DEVICE],
                    NMCLI + ["-t", "-f", _LIST_FIELDS, "device", "wifi", "list",
                             "--rescan", "no"]], STATUS_TIMEOUT_S)

    def scan(self) -> Job:
        """Outputs: a fresh network list, then the saved profiles."""
        return Job([SUDO_NMCLI + ["-t", "-f", _LIST_FIELDS, "device", "wifi", "list",
                                  "--rescan", "yes"],
                    NMCLI + ["-t", "-f", "NAME,TYPE", "connection", "show"]],
                   SCAN_TIMEOUT_S)

    def connect(self, ssid: str, password: str = "", hidden: bool = False) -> Job:
        """Join `ssid`, using a saved profile for it if there is one."""
        cmd = SUDO_NMCLI + ["device", "wifi", "connect", ssid]
        if password:
            cmd += ["password", password]
        if hidden:
            cmd += ["hidden", "yes"]
        return Job([cmd], CONNECT_TIMEOUT_S)

    def up(self, profile: str) -> Job:
        """Bring up a saved profile by name."""
        return Job([SUDO_NMCLI + ["connection", "up", "id", profile]], CONNECT_TIMEOUT_S)

    def forget(self, profile: str) -> Job:
        """Delete a saved profile, so it no longer joins at boot."""
        return Job([SUDO_NMCLI + ["connection", "delete", "id", profile]],
                   STATUS_TIMEOUT_S)

    def radio_on(self) -> Job:
        return Job([SUDO_NMCLI + ["radio", "wifi", "on"]], STATUS_TIMEOUT_S)


class FakeJob:
    """A Job that ends after a few polls with prepared output."""

    def __init__(self, outputs: List[str], message: str = "", polls: int = 2):
        self._left = polls
        self._final = Result(not message, outputs, message)
        self.result: Optional[Result] = None

    def poll(self) -> Optional[Result]:
        if self.result is None:
            self._left -= 1
            if self._left <= 0:
                self.result = self._final
        return self.result

    def cancel(self) -> None:
        if self.result is None:
            self.result = Result(False, [], "Cancelled")


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(":", "\\:")


class FakeBackend:
    """
    Pretend networks, for the desktop and the self-test.  It writes the same
    text nmcli would, so the real parsers run on it.  Every secured network's
    password is FakeBackend.PASSWORD.
    """

    PASSWORD = "password1"

    def __init__(self):
        self.radio = True
        # ssid -> (signal, secured)
        self.visible = {"Colorimeter Lab": (72, True), "Guest": (55, False),
                        "Phone hotspot": (40, True), "Home:Net": (30, True),
                        "Cafe": (22, False)}
        self.saved = ["Colorimeter Lab", "Old office"]
        self.connected: Optional[str] = "Colorimeter Lab"
        self.calls: List[str] = []

    def _list_text(self) -> str:
        return "\n".join(
            f"{'*' if ssid == self.connected else ' '}:{_esc(ssid)}:{sig}:"
            f"{'WPA2' if sec else ''}" for ssid, (sig, sec) in self.visible.items())

    def status(self) -> FakeJob:
        self.calls.append("status")
        dev = [f"GENERAL.STATE:{'100 (connected)' if self.connected else '30 (disconnected)'}",
               f"GENERAL.CONNECTION:{_esc(self.connected or '--')}",
               "GENERAL.HWADDR:DC\\:A6\\:32\\:01\\:02\\:03"]
        if self.connected:
            dev.append("IP4.ADDRESS[1]:192.168.1.23/24")
        return FakeJob(["enabled" if self.radio else "disabled", "\n".join(dev),
                        self._list_text() if self.radio else ""])

    def scan(self) -> FakeJob:
        self.calls.append("scan")
        saved = "\n".join(f"{_esc(n)}:{WIFI_TYPE}" for n in self.saved)
        return FakeJob([self._list_text(), saved + "\nWired connection 1:802-3-ethernet"])

    def connect(self, ssid: str, password: str = "", hidden: bool = False) -> FakeJob:
        self.calls.append(f"connect {ssid}")
        known = ssid in self.visible
        if not known and not hidden:
            return FakeJob([], "Not in range")
        secured = self.visible.get(ssid, (0, True))[1]
        if secured and ssid not in self.saved and password != self.PASSWORD:
            return FakeJob([], "Wrong password?")
        if ssid not in self.saved:
            self.saved.append(ssid)
        self.connected = ssid
        return FakeJob([""])

    def up(self, profile: str) -> FakeJob:
        self.calls.append(f"up {profile}")
        if profile not in self.visible:
            return FakeJob([], "Not in range")
        self.connected = profile
        return FakeJob([""])

    def forget(self, profile: str) -> FakeJob:
        self.calls.append(f"forget {profile}")
        if profile in self.saved:
            self.saved.remove(profile)
        if self.connected == profile:
            self.connected = None
        return FakeJob([""])

    def radio_on(self) -> FakeJob:
        self.calls.append("radio_on")
        self.radio = True
        return FakeJob([""])


def backend():
    """FakeBackend in simulation, so the desktop's Wi-Fi is never touched; else nmcli."""
    if os.environ.get("COLORIMETER_SIMULATION", "0") == "1":
        return FakeBackend()
    return NmcliBackend()
