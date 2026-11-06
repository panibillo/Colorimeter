#!/usr/bin/env python3
"""
baro_signal.py  —  Tell the laptop's pressure logger where a run has got to.
---------------------------------------------------------------------------
TOOL-DEVELOPMENT CODE, meant to be removed.  While the pressure transducer is
being studied, `live/baro_live.py` on lenore logs a Pico's pressure readings,
and the colorimeter tells it over UDP when to start and stop and which sample
the data belongs to, so nobody has to click the laptop's GUI mid-run.  The
protocol is set out in the pico_udp_client project,
`live/the-baro-signals-are-dynamic-pie.md`.

What is sent
------------
One tab-delimited ASCII line per datagram.  Every command but CALIBRATE
carries the sample number as its first argument:

    NEWRUN\t<samp_no>               Continue out of the ID screen
    CALIBRATE                       right after NEWRUN
    START\t<samp_no>\tReagent2      reagent 2 START
    MARK\t<samp_no>\tEndReagent2    reagent 2 countdown ran out
    STOP\t<samp_no>                 the measurement capture began
    STOP\t<samp_no>\tCanceled       CANCEL after reagent 2 START, before
                                    the measurement capture

CALIBRATE has to reach the laptop at least 10 seconds before START (Bill,
2026-11-05).  Sending it with NEWRUN meets that: START cannot come before the
pre-image screen and reagent 1's whole countdown are behind the run.

The laptop answers each with one line, `OK\t...` or `ERR\t<reason>`.  Either
answer ends the retries and is only logged: an ERR (a repeated START, say)
needs no handling here.  With no answer the line is resent, up to RESENDS more
times, RESEND_WAIT_S apart.

Where the laptop is
-------------------
Addresses and ports are not in the code or in any committed file.  They come
from config/secrets_net.py (git-ignored), the same file the pico_udp_client
project uses:

    wifidict        {network label: (ssid, password)}
    udp_dict        {(network label, machine name): (ip, port)}
    local_udp_piezo_port  the port this end sends from (0 or absent: any)

config/baro_udp.json names the machine and, optionally, the network:

    {"target": "lenore"}
    {"target": "lenore", "network": "bo basement"}

With no "network", the label is the wifidict entry whose ssid is the Wi-Fi
network this machine is connected to, looked up afresh for every line, so a
colorimeter carried from one network to another follows.  "network" fixes the
label instead, for a desktop on a wired connection.  Labels are compared
without case or surrounding spaces, since the two dicts do not always spell
them alike.  Nothing from secrets_net.py (address, port, ssid) is ever logged;
only the label and the machine name are.

How it stays out of the way
---------------------------
  * Off unless config/baro_udp.json and config/secrets_net.py both exist and
    read cleanly, and off until Arbiter.start() calls enable(), so the
    self-tests and unit tests, which never call it, send nothing even with the
    files present.
  * send() only puts the line on a queue.  One daemon thread does the network
    lookup (nmcli), the sending and the waiting, so the single-threaded UI
    loop never waits on the network.  The thread touches nothing but its own
    socket, the queue and the log.

Known limits
------------
  * The logger has one run open at a time.  If slots A and B both run a
    sample, B's NEWRUN splits A's recording and B's START is refused.  Use one
    slot at a time during these trials.
  * NEWRUN and MARK are not idempotent on the laptop while it is recording.  A
    resend after a lost *reply* (not a lost command) repeats them.
  * The laptop's port is unauthenticated; run it with --ctl-allow <Pi address>.

Removing it
-----------
Every line outside this file that belongs to this feature carries the marker
BARO-UDP, or sits between "BARO-UDP begin" and "BARO-UDP end".  `grep -rn
BARO-UDP` finds them (src/arbiter.py, src/phases.py, and a section of
sync_pi_files.txt); delete them, then this file, test/test_baro_signal.py and
config/baro_udp.json.  config/secrets_net.py stays if anything else uses it.

Public API
----------
    import baro_signal
    baro_signal.enable()                              # once, at app start
    baro_signal.send("START", "ZZ1001", "Reagent2")   # returns at once
    baro_signal.send("CALIBRATE")                     # no sample number

Run tests:
    python test/run_tests.py test_baro_signal
"""

from __future__ import annotations

import json
import logging
import queue
import runpy
import socket
import subprocess
import threading
from pathlib import Path
from typing import Optional, Tuple

import wifi

log = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
CONFIG_PATH = _CONFIG_DIR / "baro_udp.json"
SECRETS_PATH = _CONFIG_DIR / "secrets_net.py"

RESEND_WAIT_S = 1.0     # how long to wait for a reply before resending
RESENDS = 5             # resends after the first try, so six sends at most
NMCLI_TIMEOUT_S = 10.0  # for the which-network-am-I-on lookup

_queue: Optional[queue.Queue] = None     # None = disabled


def enable(path: Path = CONFIG_PATH, secrets_path: Path = SECRETS_PATH) -> bool:
    """
    Read the settings and start the sending thread.

    Args:
        path:         config/baro_udp.json.  Tests pass their own.
        secrets_path: config/secrets_net.py.  Tests pass their own.

    Returns:
        True if signals will now be sent; False if either file is missing or
        unreadable, or the secrets name no address for the target, in which
        case send() stays a no-op.
    """
    global _queue
    if _queue is not None:
        return True
    try:
        cfg = json.loads(Path(path).read_text())
        target = str(cfg["target"])
        network = cfg.get("network")
    except FileNotFoundError:
        log.info("[baro] off: no %s", path)
        return False
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        log.warning("[baro] off: cannot read %s: %s", path, e)
        return False
    try:
        ns = runpy.run_path(str(secrets_path))
        wifidict, udp_dict = dict(ns["wifidict"]), dict(ns["udp_dict"])
        local_port = int(ns.get("local_udp_piezo_port", 0))
    except FileNotFoundError:
        log.info("[baro] off: no %s", secrets_path)
        return False
    except Exception as e:                # the file is Python: anything can go wrong
        # The type only: the message could quote a line of the secrets file.
        log.warning("[baro] off: cannot read %s (%s)", secrets_path,
                    type(e).__name__)
        return False
    if not any(_same(m, target) for _label, m in udp_dict):
        log.warning("[baro] off: no udp_dict entry for %r in %s",
                    target, secrets_path)
        return False

    q: queue.Queue = queue.Queue()
    threading.Thread(target=_worker,
                     args=(q, target, network, wifidict, udp_dict, local_port),
                     name="baro_signal", daemon=True).start()
    _queue = q
    log.info("[baro] on: signals go to %s on %s", target,
             repr(network) if network else "the connected Wi-Fi network")
    return True


def send(command: str, *args: str) -> None:
    """
    Queue one signal for the laptop.  Returns at once; does nothing when off.

    `args` is the sample number and any text after it, or nothing at all for
    CALIBRATE.  The parts are joined with tabs.  A tab or line break inside a part would
    break the line apart on the laptop, so each becomes a space.

    Example:
        >>> send("START", "ZZ1001", "Reagent2")   # sends START\\tZZ1001\\tReagent2
        >>> send("CALIBRATE")                     # sends CALIBRATE
    """
    if _queue is None:
        return
    parts = [command, *args]
    _queue.put("\t".join(_one_line(p) for p in parts))


def _one_line(part) -> str:
    """One part of a signal, with any tab or line break made a space."""
    return str(part).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _same(a, b) -> bool:
    """Two labels match regardless of case and surrounding spaces."""
    return str(a).strip().lower() == str(b).strip().lower()


def _connected_ssid() -> Optional[str]:
    """The Wi-Fi network this machine is connected to, or None."""
    try:
        out = subprocess.run(
            wifi.NMCLI + ["-t", "-f", wifi._LIST_FIELDS, "device", "wifi",
                          "list", "--rescan", "no"],
            capture_output=True, text=True, timeout=NMCLI_TIMEOUT_S).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return next((n.ssid for n in wifi.parse_networks(out) if n.in_use), None)


def _address(target: str, network: Optional[str], wifidict: dict,
             udp_dict: dict) -> Tuple[Optional[Tuple[str, int]], str]:
    """
    Where to send, and the network label it was found under (for the log).

    Returns (None, why) when there is no address; `why` never holds a
    secret.
    """
    if network:
        label = network
    else:
        ssid = _connected_ssid()
        if ssid is None:
            return None, "not on Wi-Fi"
        label = next((k for k, v in wifidict.items() if v and v[0] == ssid), None)
        if label is None:
            return None, "Wi-Fi network not in wifidict"
    for (lbl, machine), (ip, port) in udp_dict.items():
        if _same(lbl, label) and _same(machine, target):
            return (str(ip), int(port)), str(label).strip()
    return None, f"no udp_dict entry for ({str(label).strip()!r}, {target!r})"


def _shown(line: str) -> str:
    """The line as it goes in the log, tabs visible."""
    return line.replace("\t", "\\t")


def _worker(q: queue.Queue, target: str, network: Optional[str],
            wifidict: dict, udp_dict: dict, local_port: int) -> None:
    """Send each queued line in turn, resending until something answers."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if local_port:
        try:
            sock.bind(("", local_port))
        except OSError as e:              # in use, say: any port will do
            log.warning("[baro] cannot send from local_udp_piezo_port (%s); "
                        "using any free port", e.strerror)
    sock.settimeout(RESEND_WAIT_S)
    while True:
        line = q.get()
        # Looked up per line, so a colorimeter moved to another network, or
        # one not yet on the network when the app started, still finds it.
        addr, where = _address(target, network, wifidict, udp_dict)
        if addr is None:
            log.warning("[baro] %s -> not sent: %s", _shown(line), where)
            continue
        _drain(sock)
        reply = "no reply"
        for _ in range(1 + RESENDS):
            try:
                sock.sendto(line.encode("ascii", "replace"), addr)
                data, _sender = sock.recvfrom(1024)
                reply = _shown(data.decode("ascii", "replace").strip())
                break
            except socket.timeout:
                continue
            except OSError as e:          # e.g. network unreachable
                reply = e.strerror or type(e).__name__
                break
        log.info("[baro] %s -> %s (%s on %s)", _shown(line), reply, target, where)


def _drain(sock: socket.socket) -> None:
    """
    Throw away replies still waiting from the previous line.

    A reply that arrives just after a resend has gone out is followed by a
    second reply to the resend.  Left in the socket, that one would be read as
    the answer to the next line.
    """
    sock.setblocking(False)
    try:
        while True:
            sock.recvfrom(1024)
    except OSError:                       # nothing left (BlockingIOError)
        pass
    finally:
        sock.settimeout(RESEND_WAIT_S)
