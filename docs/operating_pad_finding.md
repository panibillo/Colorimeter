# Operating the instrument with pad finding and strip exposures (detour stages 1 and 3)

Written 2026-11-14, for the code in the commit "Detour stage 1: find the pad
on the Pi". This file grows with the detour; section 6 covers stage 3, where
exposures are set from the strip itself. The plan is in the "Detour" section of
`docs/work_plan_chain_poc_2026-11-09.md`.

## 1. What changed, in one paragraph

Before each pre-image sweep and each measurement (post-image) sweep, the
instrument now finds the pad by itself. It flashes the photo_red LED for one
10 ms frame, with no warm-up, and fits the pad's edges after the LED is off.
The measurement sweep's fit is guided by the pre-image's fit, read back from
the same archive. The fit goes into the archive, in a `pad` group in each
section. **The operator's steps don't change.** Stage 3 then sets each
LED's exposure_us on the fitted pad and gray chips (section 6).

What the operator will notice:

- A short red flash before each sweep's first LED. photo_red is on for
  0.06–0.09 s.
- About 0.5–1.0 s more per sweep (measured on rpizero3, 2026-11-14).
- The status line under the image shows "Finding the pad (photo_red)", then
  "pad: found" or what went wrong.

## 2. Putting the code on the Pi

rpizero3 already runs this code: it was copied there for the test on
2026-11-14, and the service was restarted. For another unit, or after
further changes:

```bash
# desktop, from ~/Projects/Colorimeter
./sync_to_pi.sh rpizero3
ssh rpizero3 'sudo systemctl restart colorimeter'
```

`sync_pi_files.txt` lists `src/padfind.py`. Without it the program stops at
start-up with an import error, so a unit that doesn't start after a sync
is worth checking for that first.

The database does not change in this stage. Archives written from now on
are schema 6. Desktop tools at this commit read schema 1–6; an older
desktop checkout will refuse them, so pull before analyzing.

## 3. Running a sample

Exactly as before: load the cassette, take the pre-image, run the reagents,
take the measurement capture.

**If the status line shows a problem instead of "pad: found",** the sweep
goes on anyway, and the problem is stored in the archive. What the messages
mean:

| Message starts with | Meaning | What to do |
|---|---|---|
| `tilt` | The pad leans more than 2° | Reseat the strip square in the cassette and repeat the pre-image, if the run allows it. Otherwise carry on and note it. |
| `width` | The fitted width is more than 2 px off 41.35 px | Check the strip is a standard pad and sits flat; carry on. |
| `edge scatter` | The edges are ragged (scatter over 0.5 px) | Look at the strip for damage or debris; carry on. |
| `pad not found: ...` | No fit at all, for example the cassette is missing or the gray chips are too dark | Check the cassette is in and the LED bank works. |
| `no photo_red LED on this bank` | The LED bank has no photo_red channel | Pad finding is skipped. Only on a unit with a different LED bank. |

On rpizero3 the old 1 µg/L test pad reads a tilt of −1.6 to −1.7°, which
passes but is close to the 2° limit. The archives so far range −1.06° to
+0.06°.

## 4. Checking a run afterwards

**On the Pi,** the log has two lines per sweep:

```bash
ssh rpizero3 'grep -E "pad \((pre|post)\)|pad finding \(" ~/pi/Colorimeter/src/colorimeter.log | tail -4'
```

For example:

```
pad (pre): L= 81.80 R=122.67 W=40.87 | top= 40.00 bot= 93.34 | tilt=-1.60 deg rms=0.347 px n=48-0 top_resid=+0.89 guided/window
pad finding (pre): total 0.992 s, LED on 0.078 s, fit 0.0874 s, 2 lit frame(s), rule SensorTimestamp
```

L, R, top and bot are the pad's edges in layer px (one layer px is two
sensor px) within the bound crop. W is the width, `rms` the scatter of the
edge points about the fitted lines, and `rule SensorTimestamp` says the frame
used was confirmed to have started after the LED came on.

**On the desktop,** after pulling the archive (`./pull_captures.sh rpizero3
C10xx`):

```bash
.venv/bin/python3 - captures/C10xx.h5 <<'PY'
import sys, h5py
with h5py.File(sys.argv[1], "r") as f:
    for sec in ("/pre/pad", "/pad"):
        if sec in f:
            a = f[sec].attrs
            print(sec, "|", a.get("describe", a.get("error")),
                  "| qc:", a.get("qc", "") or "ok")
PY
```

`padfind.PadFit.from_attrs(f["/pad/after_offset"].attrs)` rebuilds the fit
the analysis would use (`.mask()` gives the pad's pixels). The `R` dataset in
each `pad` group is the frame the fit was made from, so the fit can be
redone and checked on the desktop.

## 5. Bench check without the touchscreen program

`dev/padfind_pi_check.py` runs pre + post sweeps on whatever strip is in
the cassette, photo_red only, and prints each fit and its timings. Archives go
to `~/pi/Colorimeter/captures_padfind_test/`, never to `captures/`. It isn't
in `sync_pi_files.txt` (no `dev/` script but camtest8 is), so copy it first:

```bash
# desktop
scp dev/padfind_pi_check.py rpizero3:~/pi/Colorimeter/dev/
# Pi, from ~/pi/Colorimeter; the program must be stopped, it holds the camera
sudo systemctl stop colorimeter
timeout -k 10 300 .venv/bin/python3 -u dev/padfind_pi_check.py 2
sudo systemctl start colorimeter
```

`2` is the number of pre + post pairs. Afterwards, `pgrep -af padfind_pi_check`
should list nothing (apart from the pgrep line itself).

## 6. Exposures set from the strip (stage 3, built 2026-11-14)

The pre-image and the measurement now choose each LED's exposure_us from the
strip itself (`src/exposure_rule.py`). **The operator's steps still don't
change.** What the operator will notice:

- Each LED stays on about 3 s (2 s warm-up, then three frames). Green and
  photo_red can take a fourth, longer frame (Bus) when part of the pad is
  dim; that adds about 0.5–0.8 s.
- The status line shows each try, e.g. "try 1: 16070 µs, accept (B max
  892, 0 clipped)", and "Tus frame 2 (16070 µs)", "Bus 43685 µs ...".
- **"STILL CLIPPED after 4 tries — LED marked failed"** means that LED's
  frames are saved but clip on the pad or chips. Carry on and note it; it
  should not happen on a normal strip.

Checking a run afterwards: `dev/exposure_rule_pi_check.py` prints the same
report for a test strip (see its docstring; run it like section 5's script,
with the service stopped). For a sample's archive, each LED's
`led_metadata` holds a `strip_rule` record with every try and every stored
frame's statistics.

Settings are still constants at the top of `src/exposure_rule.py` (three
frames at Tus, one Bus frame) until stage 4 moves them into the config table.
To go back to the old rule, set `CaptureConfig.exposure_rule` to "group" in
code; there is no menu setting for it yet.

## 7. Coming next (stage 4, not built yet)

- The settings above become config rows: `protocol.multiple_exposure_count`
  (3 for the trial yellow/brown strips, 1 in routine use) and
  `protocol.bus_exposure_count` (1 while studying the exposure rule, N for
  the yellow/brown trials), and the limits and re-aim factors.
- Each frame will be logged in a new `exposure_log` table.
- Each LED's last accepted Tus becomes its seed for the next pre-image.
