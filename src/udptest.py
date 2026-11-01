#!/usr/bin/env python3
"""
udptest.py  —  Send one control line to the laptop's pressure logger, verbosely.
--------------------------------------------------------------------------
A standalone debugging tool for the same job src/baro_signal.py does inside the
app.  It imports nothing from the Colorimeter project, so it can be copied
anywhere on the Pi and run by hand.

It finds the laptop the way baro_signal.py does: config/baro_udp.json names the
machine, config/secrets_net.py holds the addresses.  Every step is printed, so
you can see where a failure happens: config, Wi-Fi lookup, address lookup,
send, or reply.

Network names, passwords, addresses and ports are NOT printed unless you add
--reveal (and even then the Wi-Fi name and password are never printed).

Commands (one tab-delimited line per datagram; END is sent as STOP):

    udptest.py -n  SAMP                 NEWRUN<TAB>SAMP
    udptest.py -s  SAMP [TEXT]          START<TAB>SAMP[<TAB>TEXT]
    udptest.py -m  SAMP [TEXT]          MARK<TAB>SAMP[<TAB>TEXT]
    udptest.py -e  SAMP                 STOP<TAB>SAMP
    udptest.py --raw 'START\\tfirst'     any line; \\t becomes a tab

Plan-style lines (no sample number on START/MARK/STOP; the number is set only
by NEWRUN) are sent with --plan:

    udptest.py --plan -s TEXT           START<TAB>TEXT
    udptest.py --plan -m TEXT           MARK<TAB>TEXT
    udptest.py --plan -e                STOP

Examples:
    udptest.py -n samp1
    udptest.py -s samp1 text1
    udptest.py -m samp1 valve_open
    udptest.py -e samp1
    udptest.py --check                  look up the laptop, send nothing
    udptest.py --listen 20              just print anything that arrives, 20 s

Run it from the project venv:  python udptest.py -s samp1 text1
"""

from __future__ import annotations

import argparse
import json
import runpy
import socket
import subprocess
import sys
import time
from pathlib import Path

RESEND_WAIT_S = 1.0      # same as baro_signal.py
RESENDS = 5              # resends after the first try
NMCLI_TIMEOUT_S = 10.0

HERE = Path(__file__).resolve().parent


def say(msg: str) -> None:
    """Print one status line now (unbuffered, so ssh shows it at once)."""
    print(msg, flush=True)


def shown(data) -> str:
    """Text for a line or datagram with tabs and line ends made visible."""
    if isinstance(data, bytes):
        data = data.decode("ascii", "replace")
    return data.replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")


def hide(value, reveal: bool) -> str:
    """A secret-ish value as it may be printed."""
    return str(value) if reveal else "<hidden>"


def same(a, b) -> bool:
    """Labels match regardless of case and surrounding spaces."""
    return str(a).strip().lower() == str(b).strip().lower()


def one_line(part) -> str:
    """A part of a line with tabs and line breaks made spaces."""
    return str(part).replace("\t", " ").replace("\r", " ").replace("\n", " ")


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Send one UDP control line to the pressure logger and "
                    "show the reply, with verbose debugging output.",
        epilog="Exactly one of -n -s -m -e --raw --check --listen is needed.")
    g = p.add_argument_group("command (pick one)")
    g.add_argument("-n", "--newrun", nargs=1, metavar="SAMP",
                   help="send NEWRUN<TAB>SAMP")
    g.add_argument("-s", "--start", nargs="+", metavar=("SAMP", "TEXT"),
                   help="send START<TAB>SAMP[<TAB>TEXT]  (with --plan: "
                        "START<TAB>TEXT)")
    g.add_argument("-m", "--mark", nargs="+", metavar=("SAMP", "TEXT"),
                   help="send MARK<TAB>SAMP[<TAB>TEXT]  (with --plan: "
                        "MARK<TAB>TEXT)")
    g.add_argument("-e", "--end", nargs="*", metavar="SAMP",
                   help="send STOP<TAB>SAMP  (with --plan: just STOP)")
    g.add_argument("--raw", metavar="LINE",
                   help="send LINE exactly; write \\t for a tab")
    g.add_argument("--check", action="store_true",
                   help="do every lookup, report, but send nothing")
    g.add_argument("--listen", type=float, metavar="SECONDS",
                   help="send nothing; print datagrams arriving on the "
                        "local port for SECONDS")

    o = p.add_argument_group("options")
    o.add_argument("--plan", action="store_true",
                   help="plan-style lines: no sample number on START, MARK, "
                        "STOP")
    o.add_argument("--config", metavar="FILE",
                   help="baro_udp.json (default: search config/ near here)")
    o.add_argument("--secrets", metavar="FILE",
                   help="secrets_net.py (default: search config/ near here)")
    o.add_argument("--target", metavar="NAME",
                   help="machine name; overrides the config file")
    o.add_argument("--network", metavar="LABEL",
                   help="network label, skipping the Wi-Fi lookup; "
                        "overrides the config file")
    o.add_argument("--to", metavar="IP:PORT",
                   help="send here, skipping config and secrets entirely")
    o.add_argument("--resends", type=int, default=RESENDS, metavar="N",
                   help=f"resends after the first try (default {RESENDS})")
    o.add_argument("--wait", type=float, default=RESEND_WAIT_S, metavar="S",
                   help=f"seconds to wait for a reply (default "
                        f"{RESEND_WAIT_S})")
    o.add_argument("--no-bind", action="store_true",
                   help="do not bind local_udp_piezo_port; let the system pick")
    o.add_argument("--reveal", action="store_true",
                   help="print the address, port and network label "
                        "(never the Wi-Fi name or password)")
    return p.parse_args()


def build_line(a: argparse.Namespace) -> str | None:
    """The line to send for the chosen command, or None for check/listen."""
    if a.raw is not None:
        return a.raw.replace("\\t", "\t")
    if a.newrun:
        return "\t".join(["NEWRUN", *map(one_line, a.newrun)])
    if a.start:
        return "\t".join(["START", *map(one_line, a.start)])
    if a.mark:
        return "\t".join(["MARK", *map(one_line, a.mark)])
    if a.end is not None:
        if a.plan:
            return "STOP"
        if len(a.end) != 1:
            sys.exit("-e needs one SAMP (or use --plan -e for a bare STOP)")
        return "\t".join(["STOP", one_line(a.end[0])])
    return None


# --------------------------------------------------------------------------
# Finding the laptop
# --------------------------------------------------------------------------

def find_file(name: str, given: str | None) -> Path | None:
    """The file, from --option or a search of the usual config folders."""
    if given:
        return Path(given)
    for base in (HERE.parent, HERE, Path.cwd(), Path.cwd().parent):
        cand = base / "config" / name
        if cand.exists():
            return cand
    return None


def connected_ssid() -> str | None:
    """The Wi-Fi network this machine is connected to (never printed)."""
    cmd = ["nmcli", "-t", "-f", "IN-USE,SSID", "device", "wifi", "list",
           "--rescan", "no"]
    say(f"  running: {' '.join(cmd)}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=NMCLI_TIMEOUT_S)
    except FileNotFoundError:
        say("  nmcli is not installed")
        return None
    except subprocess.TimeoutExpired:
        say("  nmcli timed out")
        return None
    say(f"  nmcli exit code {r.returncode}, {len(r.stdout.splitlines())} "
        f"networks listed")
    if r.returncode != 0:
        say(f"  nmcli complained: {r.stderr.strip()[:120]!r}")
    for ln in r.stdout.splitlines():
        # Terse mode: "*:ssid"; a colon inside the ssid is written "\:".
        if ln.startswith("*:"):
            return ln[2:].replace("\\:", ":")
    return None


def locate(a: argparse.Namespace):
    """
    Work out (ip, port, local_port) step by step, printing each step.

    Returns None when the laptop cannot be found.
    """
    rv = a.reveal
    if a.to:
        try:
            ip, port = a.to.rsplit(":", 1)
            say(f"[1] --to given: {hide(ip, rv)}:{hide(port, rv)} "
                f"(config and secrets skipped)")
            return ip, int(port), 0
        except ValueError:
            sys.exit("--to must look like 192.168.1.20:9500")

    say("[1] config file")
    cfg_path = find_file("baro_udp.json", a.config)
    if cfg_path is None or not cfg_path.exists():
        say("  FAIL: baro_udp.json not found (try --config FILE)")
        return None
    say(f"  using {cfg_path}")
    try:
        cfg = json.loads(cfg_path.read_text())
        target = a.target or str(cfg["target"])
        network = a.network or cfg.get("network")
    except (OSError, ValueError, KeyError, TypeError) as e:
        say(f"  FAIL: cannot read it ({type(e).__name__})")
        return None
    say(f"  target machine: {target}")
    say(f"  fixed network label: "
        f"{hide(network, rv) if network else 'none (look up Wi-Fi)'}")

    say("[2] secrets file")
    sec_path = find_file("secrets_net.py", a.secrets)
    if sec_path is None or not sec_path.exists():
        say("  FAIL: secrets_net.py not found (try --secrets FILE)")
        return None
    say(f"  using {sec_path}  (contents are not printed)")
    try:
        ns = runpy.run_path(str(sec_path))
        wifidict = dict(ns["wifidict"])
        udp_dict = dict(ns["udp_dict"])
        local_port = int(ns.get("local_udp_piezo_port", 0))
    except Exception as e:      # the file is Python; print only the type
        say(f"  FAIL: cannot read it ({type(e).__name__})")
        return None
    say(f"  wifidict entries: {len(wifidict)}")
    say(f"  udp_dict entries: {len(udp_dict)}, "
        f"{sum(1 for _l, m in udp_dict if same(m, target))} for {target!r}")
    say(f"  local_udp_piezo_port set: {'yes' if local_port else 'no'} "
        f"({hide(local_port, rv)})")

    say("[3] which network")
    if network:
        label = network
        say("  using the fixed label from the config")
    else:
        ssid = connected_ssid()
        if ssid is None:
            say("  FAIL: not connected to Wi-Fi (or nmcli could not tell)")
            return None
        say("  connected to a Wi-Fi network (name not printed)")
        label = next((k for k, v in wifidict.items() if v and v[0] == ssid),
                     None)
        if label is None:
            say("  FAIL: that network is not in wifidict")
            return None
        say(f"  matched a wifidict entry, label {hide(label, rv)}")

    say("[4] address")
    for (lbl, machine), (ip, port) in udp_dict.items():
        if same(lbl, label) and same(machine, target):
            say(f"  found {target!r} on that label: "
                f"{hide(ip, rv)}:{hide(port, rv)}")
            return str(ip), int(port), local_port
    say(f"  FAIL: no udp_dict entry for (label, {target!r}). Labels are "
        f"compared without case or spaces.")
    return None


# --------------------------------------------------------------------------
# Network work
# --------------------------------------------------------------------------

def local_info(sock: socket.socket, addr) -> None:
    """Report which local address the system would use to reach addr."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(addr)             # UDP connect sends nothing
        say(f"  route out uses local address {probe.getsockname()[0]}")
    except OSError as e:
        say(f"  no route to the laptop: {e.strerror}")
    finally:
        probe.close()


def make_socket(local_port: int, a: argparse.Namespace) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if local_port and not a.no_bind:
        try:
            sock.bind(("", local_port))
            say(f"  bound local port {hide(local_port, a.reveal)}")
        except OSError as e:
            say(f"  could not bind local_udp_piezo_port ({e.strerror}); "
                f"using any free port")
    else:
        say("  not binding a fixed local port")
    return sock


def send_and_wait(sock: socket.socket, addr, line: str,
                  a: argparse.Namespace) -> bool:
    """Send with resends like baro_signal.py. True if a reply arrived."""
    payload = line.encode("ascii", "replace")
    say(f"  line: {shown(line)}")
    say(f"  bytes ({len(payload)}): {payload.hex(' ')}")

    # Throw away old replies, as baro_signal does.
    sock.setblocking(False)
    stale = 0
    try:
        while True:
            sock.recvfrom(1024)
            stale += 1
    except OSError:
        pass
    sock.settimeout(a.wait)
    if stale:
        say(f"  discarded {stale} stale datagram(s) waiting on the socket")

    for n in range(1 + a.resends):
        t0 = time.monotonic()
        try:
            sent = sock.sendto(payload, addr)
            say(f"  try {n + 1}/{1 + a.resends}: sent {sent} bytes, "
                f"waiting up to {a.wait:g} s ...")
            data, sender = sock.recvfrom(1024)
        except socket.timeout:
            say(f"  try {n + 1}: no reply after "
                f"{time.monotonic() - t0:.2f} s")
            continue
        except OSError as e:
            say(f"  try {n + 1}: network error: {e.strerror or e}")
            return False
        dt = time.monotonic() - t0
        same_host = sender[0] == addr[0]
        say(f"  REPLY after {dt:.3f} s from "
            f"{hide(sender[0], a.reveal)}:{hide(sender[1], a.reveal)} "
            f"({'the laptop' if same_host else 'SOMEONE ELSE'})")
        say(f"  reply bytes ({len(data)}): {data.hex(' ')}")
        say(f"  reply text: {shown(data)}")
        word = data.decode("ascii", "replace").split("\t", 1)[0].strip()
        if word.upper() == "OK":
            say("  result: OK")
        elif word.upper() == "ERR":
            say("  result: ERR (the laptop refused it; see the reason above)")
        else:
            say("  result: unrecognized reply")
        return True
    say("  GIVE UP: no reply. The line may or may not have arrived.")
    say("  Likely causes: wrong address/port, laptop firewall, "
        "baro_live.py started without --ctl-port, or --ctl-allow not "
        "set to this Pi's address.")
    return False


def listen(local_port: int, seconds: float, a: argparse.Namespace) -> None:
    """Print whatever arrives on the local port for a while."""
    sock = make_socket(local_port, a)
    sock.settimeout(0.5)
    say(f"listening for {seconds:g} s (Ctrl-C to stop)")
    end = time.monotonic() + seconds
    got = 0
    try:
        while time.monotonic() < end:
            try:
                data, sender = sock.recvfrom(2048)
            except socket.timeout:
                continue
            got += 1
            say(f"  from {hide(sender[0], a.reveal)}: {shown(data)}")
    except KeyboardInterrupt:
        pass
    say(f"heard {got} datagram(s)")


def main() -> int:
    a = parse_args()
    line = build_line(a)
    chosen = [line is not None, a.check, a.listen is not None]
    if sum(chosen) != 1:
        sys.exit("give exactly one of -n -s -m -e --raw --check --listen")

    say(f"udptest {time.strftime('%Y-%m-%d %H:%M:%S')} on "
        f"{socket.gethostname()}, python {sys.version.split()[0]}")

    found = locate(a) if not (a.listen is not None and not a.to) else None
    if a.listen is not None:
        # Only the local port matters here; try for it, but do not require it.
        lp = found[2] if found else 0
        if not found:
            say("(no address lookup done for --listen; not binding a port "
                "unless --to was given)")
        listen(lp, a.listen, a)
        return 0
    if found is None:
        say("RESULT: could not find the laptop. Nothing sent.")
        return 2
    ip, port, local_port = found
    addr = (ip, port)

    say("[5] socket")
    sock = make_socket(local_port, a)
    local_info(sock, addr)
    if a.check:
        say("RESULT: lookups succeeded. --check sends nothing.")
        return 0

    say("[6] send")
    ok = send_and_wait(sock, addr, line, a)
    say(f"RESULT: {'reply received' if ok else 'no reply'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
