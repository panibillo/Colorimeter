#!/usr/bin/env python3
"""
tsc2007_probe.py  —  TSC2007 raw ADC diagnostic
------------------------------------------------
Tests every reasonable command-byte and byte-extraction combination.
No coordinate transforms — purely raw ADC values.

Prompts for corners, sides, and centre.  Hold each position until the
script says "released", then move to the next.

At the end, reports:
  • mean raw value per (method, position) for X, Y, Z1, Z2
  • first-sample raw bytes so you can verify the extraction formula
  • spread scores to identify which method gives position-dependent data

EXPECTED RANGES  (from TSC2007 datasheet + Adafruit panel data)
  12-bit ADC, full scale = 4095, VREF = VCC ≈ 3.3 V
  Panel edges typically read ~100–400 (near) and ~3600–3900 (far).
  Usable span: ~3400 counts.  Axes may be swapped or mirrored — that
  is normal and is fixed later by the affine transform.

  Z1 with PD=00 (power-down between reads): anomalously low (~3), because
  the TSC2007 resets its plate drive for PENIRQ between each channel read,
  corrupting the Z1 sense voltage.
  Z1 with PD=01 or PD=11 (ADC stays on): expected ~200–3000 when touching.

REFERENCE  (Adafruit 2.4" featherwing calibration program)
  TOUCH_SWAP_XY=True, TOUCH_INVERT_X=True, TOUCH_INVERT_Y=False
  → raw X channel maps to display Y (swapped), raw Y to display X
  → raw X direction is inverted relative to display X after the swap
  The 1651 2.8" shield may differ, but axis swap + inversion is common.

Usage:
  python3 dev/tsc2007_probe.py
"""

import sys
import time
import math
import smbus2
from gpiozero import DigitalInputDevice

# ── Hardware ──────────────────────────────────────────────────────────────────
I2C_BUS  = 1
I2C_ADDR = 0x48
IRQ_PIN  = 4
T_SETTLE = 0.0003    # 300 µs between write and read (ample for 12-bit conversion)

DBG1=1
DBG2=0
# ── Command byte sets ─────────────────────────────────────────────────────────
#
# Byte layout: [A3 A2 A1 A0 | PD1 PD0 | M | 0]
#
#   Function nibble (A3:A0 in bits 7:4):
#     0xC = MEASURE_X   0xD = MEASURE_Y   0xE = MEASURE_Z1   0xF = MEASURE_Z2
#     0x0 = MEASURE_TEMP0  (used for END to re-enable PENIRQ)
#
#   PD bits (bits 3:2):
#     00 (0x00) → power-down each conversion, PENIRQ re-enabled between reads
#                 KNOWN PROBLEM: resets plate drive between X/Y/Z1, making Z1≈3
#     01 (0x04) → ADC on, PENIRQ disabled  ← Adafruit library / Linux kernel
#     10 (0x08) → power-down, PENIRQ stays off
#     11 (0x0C) → always powered on, PENIRQ disabled  ← our current src/
#
#   M (bit 1): 0 = 12-bit,  1 = 8-bit
#
#   END command: TEMP0 (A3:A0=0) with PD=00 → re-enables PENIRQ after sequence
#
# name → (X_cmd, Y_cmd, Z1_cmd, Z2_cmd, END_cmd)
METHODS = {
    "PD=00/12b": (0xC0, 0xD0, 0xE0, 0xF0, 0x00),  # original; known-bad Z1
    "PD=01/12b": (0xC4, 0xD4, 0xE4, 0xF4, 0x00),  # Adafruit / Linux kernel
    "PD=11/12b": (0xCC, 0xDC, 0xEC, 0xFC, 0x00),  # always-on (our latest src/)
    # "PD=01/8b":  (0xC6, 0xD6, 0xE6, 0xF6, 0x02),  # 8-bit mode for comparison
}
CHANNELS = ("X", "Y", "Z1", "Z2")

# ── Byte-extraction methods ───────────────────────────────────────────────────
#
# TSC2007 returns 2 bytes with the result left-aligned (MSB-first):
#   12-bit: D11:D4 in byte0,  D3:D0 in byte1 bits 7:4
#           → value = (b[0]<<4) | (b[1]>>4)   range 0–4095
#    8-bit: D7:D0 in byte0
#           → value = b[0]                     range 0–255
#
EXTRACT = {
    "std12":  lambda b: ((b[0] << 4) | (b[1] >> 4)) & 0xFFF,  # standard 12-bit
    # "raw16":  lambda b: (b[0] << 8) | b[1],                    # full 16-bit word
    # "byte0":  lambda b: b[0],                                   # MSB only
    # "byte1":  lambda b: b[1],                                   # LSB only
}

# ── Test positions ────────────────────────────────────────────────────────────
POSITIONS = [
    ("TL", "TOP-LEFT corner"),
    ("TR", "TOP-RIGHT corner"),
    ("BL", "BOTTOM-LEFT corner"),
    ("BR", "BOTTOM-RIGHT corner"),
    ("CT", "CENTER"),
    ("SL", "LEFT edge, midheight"),
    ("SR", "RIGHT edge, midheight"),
    ("ST", "TOP edge, midwidth"),
    ("SB", "BOTTOM edge, midwidth"),
]

# Spatial groupings for scoring
LEFT_LOCS  = frozenset({"TL", "BL", "SL"})
RIGHT_LOCS = frozenset({"TR", "BR", "SR"})
TOP_LOCS   = frozenset({"TL", "TR", "ST"})
BOT_LOCS   = frozenset({"BL", "BR", "SB"})


# ── Low-level I2C ─────────────────────────────────────────────────────────────

def is_touched(irq) -> bool:
    """True if TSIRQ pin is asserted (active-high)."""
    return irq.value == 1

def read_one(bus, cmd):
    """
    Write one command byte, wait T_SETTLE, read 2 bytes.
    Returns (b0, b1).  Raises OSError on I2C failure.
    """
    # time.sleep(T_SETTLE) # ADDED. to ensure settled when called repeatedly.
    wr = smbus2.i2c_msg.write(I2C_ADDR, [cmd])
    rd = smbus2.i2c_msg.read(I2C_ADDR, 2)
    bus.i2c_rdwr(wr)
    time.sleep(T_SETTLE)
    bus.i2c_rdwr(rd)
    b = list(rd)
    return (b[0], b[1])


def read_set(bus, method_name):
    """
    Read all four channels with one method, then send END to re-enable PENIRQ.
    Returns {ch: (b0, b1)} — ch is None if I2C error on that channel.
    """
    cx, cy, cz1, cz2, end = METHODS[method_name]
    result = {}
    for ch, cmd in (("X", cx), ("Y", cy), ("Z1", cz1), ("Z2", cz2)):
        n,nmiss, result[ch] = 0,0,None
        try:
            # change to reading 20 pair until 2 consecutive within tol
            tol = 32
            while n<12 and result[ch] is None:
                n += 1
                result1 = read_one(bus, cmd)
                result2 = read_one(bus, cmd)
                if(  result1[0]==result2[0] and  
                 abs(result1[1]-result2[1]) <= tol ):
                    result[ch] = result2
                    if DBG2:
                        print (f"read_set({method_name})[{n:02d}] FOUND: {result1[0]}=?={result2[0]}, {result1[1]}=?={result2[1]} : {result2[1] - result1[1]}")
                else:
                    nmiss += 1
                    if DBG2:
                        print (f"read_set({method_name})[{n:02d}] diffs: {result1[0]}=?={result2[0]}, {result1[1]}=?={result2[1]} : {result2[1] - result1[1]}")
            # result[ch] = read_one(bus, cmd)
        except OSError:
            result[ch] = None
    try:
        read_one(bus, end)
    except OSError:
        pass
    if DBG1: print (f"percent misses = {100*nmiss/(max(n,1)):02.0f}%   {nmiss}/{n}")
    return result


# ── Collection ────────────────────────────────────────────────────────────────

def collect(bus, irq, label, desc):
    """
    Wait for a touch, collect samples continuously until released.
    Returns {method_name: {channel: [(b0, b1), ...]}}
    """
    print(f"\n>>> {label}: {desc}")
    print(f"    Press and HOLD until you see 'released'.")

    while not is_touched(irq):
        time.sleep(0.01)
    time.sleep(0.05)    # debounce

    data = {m: {ch: [] for ch in CHANNELS} for m in METHODS}
    n = 0
    print("    [touched — collecting...]", end="", flush=True)

    while is_touched(irq):
        for mname in METHODS:
            r = read_set(bus, mname)
            for ch in CHANNELS:
                if r[ch] is not None:
                    data[mname][ch].append(r[ch])
                    if DBG2:
                        print (f"collect {mname:8s}:{ch:2s}[{n:2d}] = {r[ch]}")
        n += 1
        if n % 10 == 0:
            print(".", end="", flush=True)
        time.sleep(0.025)

    while is_touched(irq):
        time.sleep(0.01)
    time.sleep(0.05)
    print(f" released ({n} samples)")

    # Print first-sample diagnostic for PD=01/12b
    if n > 0:
        r0 = data["PD=01/12b"]
        parts = []
        for ch in CHANNELS:
            bl = r0[ch]
            if bl:
                b0, b1 = bl[0]
                v12 = ((b0 << 4) | (b1 >> 4)) & 0xFFF
                parts.append(f"{ch}: {b0:08b}:{b1:08b} → {v12:4d}")
            else:
                parts.append(f"{ch}: ---")
        print(f"    PD=01 first sample:  " + "   ".join(parts))

    return data


# ── Statistics helpers ────────────────────────────────────────────────────────

def _mean(vals):
    return sum(vals) / len(vals) if vals else float("nan")


def _extracted(all_data, pos, mname, ch, efn):
    """All extracted values for (pos, method, channel, extraction_fn)."""
    raw = all_data.get(pos, {}).get(mname, {}).get(ch, [])
    return [efn(b) for b in raw if b is not None]


# ── Reporting ─────────────────────────────────────────────────────────────────

def report(all_data, collected_positions):
    def loc_mean(pos_set, mname, ch, efn):
        vals = []
        for pos in pos_set & set(collected_positions):
            vals.extend(_extracted(all_data, pos, mname, ch, efn))
        return _mean(vals)

    # ── Per-channel mean tables ───────────────────────────────────────────
    for ch in ("X", "Y", "Z1"):
        print(f"\n{'─'*80}")
        print(f"  Channel {ch}  —  mean raw value per position")
        print(f"{'─'*80}")
        w = 6
        hdr = ("  {:12s} {:7s} | ".format("method", "ext") +
               " ".join(f"{p:>{w}}" for p in collected_positions))
        print(hdr)
        print("  " + "─" * (len(hdr) - 2))
        for mname in METHODS:
            for ename, efn in EXTRACT.items():
                row = "  {:12s} {:7s} | ".format(mname, ename)
                for pos in collected_positions:
                    vals = _extracted(all_data, pos, mname, ch, efn)
                    m = _mean(vals)
                    row += (f"{'---':>{w}}" if math.isnan(m) else f"{m:>{w}.0f}") + " "
                print(row)

    # ── Raw byte dump (PD=01/12b, all positions, first sample) ───────────
    print(f"\n{'─'*80}")
    print("  Raw bytes — PD=01/12b — first sample per position")
    print("  Format: b0(binary):b1(binary) → std12_value")
    print(f"{'─'*80}")
    for pos in collected_positions:
        parts = []
        for ch in CHANNELS:
            bl = all_data.get(pos, {}).get("PD=01/12b", {}).get(ch, [])
            if bl:
                b0, b1 = bl[0]
                v = ((b0 << 4) | (b1 >> 4)) & 0xFFF
                parts.append(f"{ch}={b0:08b}:{b1:08b}→{v:4d}")
            else:
                parts.append(f"{ch}=---")
        print(f"  {pos:4s}: " + "   ".join(parts))

    # ── Scoring table ─────────────────────────────────────────────────────
    #
    # Four spread metrics per (method, extraction):
    #   X_lr  = |mean(X, right locs) − mean(X, left locs)|   (X channel, left/right)
    #   X_tb  = |mean(X, bot locs)   − mean(X, top locs) |   (X channel, top/bottom)
    #   Y_lr  = |mean(Y, right locs) − mean(Y, left locs)|   (Y channel, left/right)
    #   Y_tb  = |mean(Y, bot locs)   − mean(Y, top locs) |   (Y channel, top/bottom)
    #
    # Interpretation:
    #   Normal (no swap): X_lr large, Y_tb large, others small
    #   Swapped axes:     X_tb large, Y_lr large, others small
    #   One of X_lr or Y_tb being large tells you which channel = display X
    #
    print(f"\n{'═'*80}")
    print("  SPREAD SCORES  (higher is better; >200 counts is meaningful)")
    print("  X_lr = X channel, left vs right  |  X_tb = X channel, top vs bottom")
    print("  Y_lr = Y channel, left vs right  |  Y_tb = Y channel, top vs bottom")
    print(f"{'═'*80}")
    print("  {:12s} {:7s} | {:>7s} {:>7s} {:>7s} {:>7s} | {:>8s} | {}".format(
        "method", "ext", "X_lr", "X_tb", "Y_lr", "Y_tb", "Z1_avg", "verdict"))
    print("  " + "─" * 78)

    best_rows = []
    for mname in METHODS:
        for ename, efn in EXTRACT.items():
            def sp(pos_set_a, pos_set_b, ch):
                a = loc_mean(pos_set_a, mname, ch, efn)
                b = loc_mean(pos_set_b, mname, ch, efn)
                return abs(b - a) if not (math.isnan(a) or math.isnan(b)) else float("nan")

            x_lr = sp(LEFT_LOCS,  RIGHT_LOCS, "X")
            x_tb = sp(TOP_LOCS,   BOT_LOCS,   "X")
            y_lr = sp(LEFT_LOCS,  RIGHT_LOCS, "Y")
            y_tb = sp(TOP_LOCS,   BOT_LOCS,   "Y")

            z_all = []
            for pos in collected_positions:
                z_all.extend(_extracted(all_data, pos, mname, "Z1", efn))
            z_avg = _mean(z_all)

            def fmt(v, w=7):
                return f"{'---':>{w}}" if math.isnan(v) else f"{v:>{w}.0f}"

            # Verdict: which channel layout makes spatial sense?
            normal_ok = (not math.isnan(x_lr) and x_lr > 200 and
                         not math.isnan(y_tb) and y_tb > 200)
            swapped_ok = (not math.isnan(x_tb) and x_tb > 200 and
                          not math.isnan(y_lr) and y_lr > 200)
            z_ok = not math.isnan(z_avg) and z_avg > 20

            if normal_ok:
                verdict = "X=rawX Y=rawY" + (" Z✓" if z_ok else " Z✗")
            elif swapped_ok:
                verdict = "X=rawY Y=rawX (SWAPPED)" + (" Z✓" if z_ok else " Z✗")
            elif not math.isnan(x_lr) and x_lr > 200:
                verdict = "X_lr ok, Y unclear"
            elif not math.isnan(y_tb) and y_tb > 200:
                verdict = "Y_tb ok, X unclear"
            else:
                verdict = "—"

            print("  {:12s} {:7s} | {} {} {} {} | {} | {}".format(
                mname, ename,
                fmt(x_lr), fmt(x_tb), fmt(y_lr), fmt(y_tb),
                fmt(z_avg, 8), verdict))

            if normal_ok or swapped_ok:
                best_rows.append((mname, ename, x_lr, x_tb, y_lr, y_tb, z_avg, verdict))

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'─'*80}")
    if best_rows:
        print("  BEST combinations:")
        for row in best_rows:
            mname, ename, x_lr, x_tb, y_lr, y_tb, z_avg, verdict = row
            print(f"    method={mname}  ext={ename}  {verdict}")
            print(f"      X_lr={x_lr:.0f}  X_tb={x_tb:.0f}  Y_lr={y_lr:.0f}  Y_tb={y_tb:.0f}  Z1={z_avg:.1f}")
    else:
        print("  No combination showed clear spatial spread > 200 counts.")
        print("  Possible causes:")
        print("    • Hardware not connected or wrong I2C bus/address")
        print("    • All positions touching same spot (try pressing actual corners)")
        print("    • Panel wiring issue on the 1651 shield")

    print(f"\n{'─'*80}")
    print("  Per-position min/max (PD=01/12b, std12 extraction)")
    print(f"{'─'*80}")
    print(f"  {'pos':4s}  {'X_mean':>7s}  {'Y_mean':>7s}  {'Z1_mean':>8s}  {'Z2_mean':>8s}")
    efn = EXTRACT["std12"]
    for pos in collected_positions:
        xm = _mean(_extracted(all_data, pos, "PD=01/12b", "X",  efn))
        ym = _mean(_extracted(all_data, pos, "PD=01/12b", "Y",  efn))
        zm = _mean(_extracted(all_data, pos, "PD=01/12b", "Z1", efn))
        z2 = _mean(_extracted(all_data, pos, "PD=01/12b", "Z2", efn))
        def f(v): return f"{v:7.0f}" if not math.isnan(v) else f"{'---':>7}"
        print(f"  {pos:4s}  {f(xm)}  {f(ym)}  {f(zm):>8}  {f(z2):>8}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(__doc__)

    try:
        bus = smbus2.SMBus(I2C_BUS)
        irq = DigitalInputDevice(IRQ_PIN, pull_up=True)
    except Exception as e:
        print(f"Init error: {e}")
        sys.exit(1)

    # Verify TSC2007 responds at all
    try:
        b = read_one(bus, 0x00)   # TEMP0, PD=00
        v = ((b[0] << 4) | (b[1] >> 4)) & 0xFFF
        print(f"TSC2007 OK at I2C {I2C_BUS} @ 0x{I2C_ADDR:02X}"
              f"  —  TEMP0: {b[0]:08b}:{b[1]:08b} = {v}")
    except OSError as e:
        print(f"ERROR: TSC2007 not responding: {e}")
        bus.close(); irq.close(); sys.exit(1)

    print()
    print(f"Will test {len(METHODS)} command sets × {len(EXTRACT)} extractions"
          f" × {len(CHANNELS)} channels per sample.")
    print(f"Positions: {', '.join(p for p,_ in POSITIONS)}")
    print()

    all_data = {}
    collected = []

    # Verify interrupt is interpreted correctly:
    if 0:
        tf = {True:'T', False:'F'}
        try:
            now = time.monotonic()
            n = 0
            while time.monotonic() - now < 60:
                n += 1
                v = is_touched(irq)
                print(tf[v], end=' ',flush=True)
                time.sleep(0.1)
                if n%40==0:
                    print()
                    
        except KeyboardInterrupt:
            print("\n\nInterrupted — reporting with data collected so far.")
        bus.close()
        irq.close()
        return

    try:
        for pos_label, pos_desc in POSITIONS:
            all_data[pos_label] = collect(bus, irq, pos_label, pos_desc)
            collected.append(pos_label)
            print("    (Next position in 0.5 s...)")
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n\nInterrupted — reporting with data collected so far.")

    bus.close()
    irq.close()

    if not collected:
        print("No data collected.")
        sys.exit(0)

    print(f"\n{'═'*80}")
    print("ANALYSIS")
    print(f"Positions collected: {', '.join(collected)}")
    report(all_data, collected)
    print()


if __name__ == "__main__":
    main()
