# Operating the instrument with pad finding and strip exposures (detour stages 1, 3, 4a and 4b)

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

### Settings (stage 4a, 2026-11-14)

Every number of the rule is a row in the config table (Menu → Config), read
at the start of each run, before the camera starts. `src/exposure_rule.py`
explains each value and where it came from.

| Row | Factory value | What it does |
|---|---|---|
| `protocol.multiple_exposure_count` | 1 | frames kept per LED at Tus; **set it to 3 for the trial yellow/brown strips** |
| `protocol.bus_exposure_count` | 1 | frames kept per LED at Bus, when one is needed; N for the yellow/brown trials |
| `protocol.analysis_channels` | violet.B, royal_blue.B, cyan.B, green.Gb, green.Gr, green.B, photo_red.R | the channels that decide Tus and can call for Bus |
| `capture.exposure_rule` | strip | "group" goes back to the stored exposure group (the rule before 2026-11-14) |
| `capture.clip_top_dn` / `clip_bottom_dn` / `noise_floor_dn` | 1023 / 65 / 164 | the raw-DN limits |
| `capture.top_target_fraction` / `top_floor_fraction` | 0.85 / 0.80 | where a re-aim puts the brightest pixel; how bright it must be to accept |
| `capture.clip_step_factor` | 0.7 | the step after a clipped frame |
| `capture.max_exposure_tries` | 4 | tries to find Tus |
| `capture.exposure_cap_us` | 200000 | the longest exposure_us the rule may ask for |
| `capture.bus_margin` | 1.1 | how far past the floor Bus aims |
| `capture.edge_darkening_factor` | 5.0 | the edge rule of the floor test |
| `capture.edge_signal_fraction` | 0.5 | the edge rule's second condition: an edge pixel is left out only if its signal is also below this fraction of the interior's |
| `capture.padfind_led` / `padfind_exposure_us` | photo_red / 10000 | the pad-finding frame |
| `capture.padfind_offset_pre` / `_post` | 0.10,0.44,0.33,0.33 / 0.13,0.35,0.11,0.11 | the photo_red offsets (left, right, top, bottom; layer px) |

- A value that makes no sense with the others (say a noise floor above
  1023) is shown as a warning on the start-up screen ("Exposure rule"),
  and a run with it stops at once, before the camera starts, with the row
  named.
- "Restore defaults" puts the factory values back, so after one the frame
  count is 1 again.
- `capture.layer_keep_min_dn` and `layer_keep_max_dn` are not used by the
  strip rule. They stay for the group rule, the white photo and a start-up
  check.
- Menu → Advanced → Capture Suite always uses the group rule.

### The exposure_log table (stage 4b, 2026-11-14)

Every frame the strip rule measured is a row per Bayer layer in the
`exposure_log` table: the pad-finding frame, every try at Tus (rejected ones
too), and every frame kept at Tus and Bus. `src/exposure_log_repo.py`
explains every column. The rows are written once per sweep, after the last
coloured LED is off (about 0.05–0.1 s on rpizero3, 77 rows per 5-LED
sweep). A failed write is logged and the run goes on; the archive holds the
same numbers. To look at one sample on the Pi (no sqlite3 there, so Python):

```
# Pi, from ~/pi/Colorimeter
PYTHONPATH=src .venv/bin/python3 -c "
import exposure_log_repo as L
for r in L.read_rows(samp_no='C1049'):
    print(r['section'], r['purpose'], r['attempt'], r['label'], r['verdict'],
          r['LED'], r['bayer_layer'], r['exposure_us'], r['max_dn'], r['min_dn'])"
```

`dev/exposure_rule_pi_check.py` also prints the rows it wrote.

## 7. Coming next (stage 4c, not built yet)

- Always read seed exposures from the table.  Keep the table updated when 
  a seed exposure fails and is corrected on a pre-image. 
- We may keep a Bus seed for post images that will be the shortest acceptable
  exposure on the darkest expected pad.
