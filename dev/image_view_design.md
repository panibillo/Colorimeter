# Image View — design

## Purpose

Two on-instrument tools for looking at a camera image with the ROI rectangles
drawn on it:

1. **Menu → View Image** — the whole frame, with zoom, pan, and all ROIs shown.
2. **Read phase → View Sample** — the same screen, opened zoomed to the sample
   ROI plus a margin.

They exist for two reasons: to confirm that the camera, the staging, and the
ROI rectangles are all where they are supposed to be; and to edit the sample
ROI rectangle on the instrument itself.

Both take one brief white-LED exposure. Colour accuracy is not required.

Nothing like this exists yet. `dev/display_test2..6.py` prove the panel can
take a PIL image; the only ROI-overlay code in the tree is matplotlib
(`dev/analyze_camtest8.py`), which is desktop-only.

---

## Coordinate spaces

This is the part that will cause silent, hard-to-see errors if it is left
implicit, so it is written out first. Five spaces are in play.

### 1. Stream space — what a stored ROI means

A ROI's `(x, y, w, h)` is in the pixel grid of **the configured camera stream**,
origin top-left, `x` → column, `y` → row.

`BayerCam` builds two configs per detected model (`bayer_cam.py:175-187`):

| Model | Bayer pattern | CONFIG_5 (default) | CONFIG_6 |
|---|---|---|---|
| imx219 (Module 2) | BGGR | 1640 × 1232 (4:3) | 3280 × 2464 |
| imx708 (Module 3) | BGGR | 2304 × 1296 (16:9) | 4608 × 2592 |

> **Correction (step 1).** An earlier draft of this table said imx708 is RGGB.
> It is not: `_CAMERA_PROFILES["imx708"]` in `bayer_cam.py:71-79` sets
> `"bayer_pattern": "BGGR"`, with the note that the hardware delivers BGGR
> despite the sensor's native RGGB layout. **Both profiles are BGGR today.**
> `extract_bayer_layers()` still supports RGGB, so nothing may hardcode the
> pattern — pass `cam.bayer_pattern`. Note that the ROI→layer conversions in
> `roi.py` do not depend on the pattern at all: `//2` is the same arithmetic
> either way. The pattern decides only *which colour name* each layer gets,
> which is why trap A (parity relabelling the layers) is the dangerous one.

`self.config = self.CONFIG_5` at construction (`bayer_cam.py:187`). Nothing in
the app switches to CONFIG_6 today, but the mechanism is there, and the
detected model can change by swapping a camera.

**The ROI constants in `capture_runner.py:94-97` are imx708 CONFIG_5 numbers.**
They are not merely "unscaled" for another config — 1640 × 1232 is 4:3 and
2304 × 1296 is 16:9, so between models there is no scale factor at all, only a
different field of view.

*Consequence:* every stored ROI records the frame size (and model) it was
defined against. See `frame_w` / `frame_h` / `model` in the `roi` table.

### 2. RGB array — what the viewer draws

`rgb, bayer, meta = cam.capture_frame()` (`bayer_cam.py:268`).

`rgb` is `request.make_array("main")`: shape **(H, W, 3), uint8, BGR order**.
Note BGR, not RGB — `to_display_image` reverses it with `[:, :, ::-1]`
(`bayer_cam.py:770`).

Indexing is `rgb[y, x, channel]` — **row first**. A ROI slices as
`rgb[y:y+h, x:x+w]`. Same H, W as the configured stream, so ROI coordinates
apply 1:1 with no conversion. This is the space the viewer works in.

### 3. Bayer mosaic

`bayer` is `unpack_raw10(raw_bytes, ...)` (`bayer_cam.py:312`): shape
**(H, W), uint16, values 0..1023**. Same H, W as the RGB array, so a ROI
slices identically: `bayer[y:y+h, x:x+w]`.

### 4. Bayer layer — the tricky one

`BayerCam.extract_bayer_layers(bayer, pattern)` (`bayer_cam.py:604`) returns
four arrays, each shape **(H//2, W//2)**, by strided slicing:

```python
# called by capture_runner; roi.py's converters are pattern-independent
BGGR (both current profiles):
    B =[0::2, 0::2]   Gb=[0::2, 1::2]   Gr=[1::2, 0::2]   R =[1::2, 1::2]
RGGB (supported, not currently produced):
    R =[0::2, 0::2]   Gr=[0::2, 1::2]   Gb=[1::2, 0::2]   B =[1::2, 1::2]
```

Layer pixel `(ly, lx)` is mosaic pixel `(2·ly + py, 2·lx + px)`, where
`(py, px)` is that layer's phase offset, 0 or 1 in each axis.

Three traps live here.

**Trap A — parity decides colour identity.** Extracting layers from a crop that
starts at an odd row or column relabels every layer. Under RGGB, a crop
starting at an odd `x` makes `[0::2, 0::2]` return Gr, not R — but the dict key
still says `'R'`. `_roi_bound_layers` (`capture_runner.py:306-316`) crops
**then** extracts, so `ROI_BOUND`'s origin parity fixes the colour identity of
everything written to the archive. `ROI_BOUND = (1090, 360, 160, 630)` is even
on both axes, so it is correct today — by luck, not by check.

**Trap B — `//2` truncation.** `_channel_roi` (`capture_runner.py:210-213`) is
`(x//2, y//2, w//2, h//2)`. An odd `x` floors to a window that starts one
mosaic column *early*; an odd `w` silently drops the last column from every
layer.

→ **`x`, `y`, `w`, `h` must all be even.** Enforced by `roi.snap_even()` on
every save.

**Trap C — two different layer origins.** `capture_runner` extracts layers from
two sources, and they need *different* ROI conversions:

| Source | Shape | ROI conversion | Used for |
|---|---|---|---|
| `_extract(bayer)` (line 534) | (H//2, W//2) | `(x//2, y//2, w//2, h//2)`, absolute | all exposure analysis (683, 749, 793) |
| `_bound_layers(bayer)` (line 539) | (bound.h//2, bound.w//2) | `((x−bound.x)//2, (y−bound.y)//2, w//2, h//2)` | the ROI crops written to the archive (897, 921, 1042, 1059) |

An absolute ROI indexed into bound-crop layers reads the wrong pixels or an
empty array. `dev/analyze_camtest8.py:59-62` computes the relative form by
hand. The current code does not mix them up — verified — but nothing prevents
it, because both conversions are anonymous `//2` arithmetic at the call site.
`roi.py` gives each a name.

**Statistics footnote.** A ROI of `w × h` stream pixels yields
`(w//2) × (h//2)` samples **per layer**. The 68 × 68 sample ROI is 34 × 34 =
1156 pixels per layer — and the two green layers are two separate
1156-pixel populations, not one of 2312. Worth remembering when judging
whether an edited ROI is large enough.

### 5. Canvas space

The viewer's own 200 × 240 display pixels. Related to stream space by the view
box and scale:

```
s        = canvas_w / view.w
canvas_x = (image_x − view.x) · s
canvas_y = (image_y − view.y) · s
```

Never persisted.

---

## The `roi` table

`exposure_repo.py` is the template — `active` flag, `add_dt`, supersede rather
than delete, self-test against a throwaway copy of the DB. The same idiom
gives ROIs versioning, restore, and history with no new machinery.

```sql
-- added to src/db_DDL.sql
CREATE TABLE IF NOT EXISTS roi (
    rowid     INTEGER PRIMARY KEY NOT NULL,
    active    INTEGER NOT NULL DEFAULT 1,  -- 1 = in force, 0 = superseded (history)
    isdefault INTEGER NOT NULL DEFAULT 0,  -- 1 = commissioned fallback, never edited
    add_dt    TEXT,                        -- local timestamp of the write
    name      TEXT NOT NULL,               -- identifier: 'sample','white','black1',...
    role      TEXT,                        -- what analysis does with it
    x         INTEGER NOT NULL,
    y         INTEGER NOT NULL,
    w         INTEGER NOT NULL,
    h         INTEGER NOT NULL,
    frame_w   INTEGER NOT NULL,            -- stream size these coordinates assume
    frame_h   INTEGER NOT NULL,
    model     TEXT,                        -- 'imx708' | 'imx219'
    color     TEXT,                        -- palette name for the overlay
    remark    TEXT
);
```

Each `name` has two rows at seed time: `isdefault=1, active=0` (the fallback,
never edited) and `isdefault=0, active=1` (in force). `WHERE active = 1` always
returns exactly one row per name. An edit flips the old active row to 0 and
inserts a new one — prior geometry is kept, as calibrations are.

Seed values, from `capture_runner.py:94-97` (imx708, 2304 × 1296). All are
already even, so the seed needs no adjustment:

| name | role | x | y | w | h |
|---|---|---|---|---|---|
| sample | sample | 1150 | 790 | 68 | 68 |
| white | white | 1150 | 496 | 136 | 68 |
| black1 | black | 1150 | 380 | 68 | 68 |
| black2 | black | 1150 | 900 | 68 | 68 |
| bound | bound | 1090 | 360 | 160 | 630 |

`ROI_BLACK` is currently defined but never read anywhere in `src/`. It is
carried into the table and displayed, but it is not wired into the capture
path.

### Frame-size mismatch on read

`load_active(frame_w, frame_h, model)`:

1. Sizes match → use the row as stored.
2. Same model, both axes scale by the same factor (CONFIG_5 ↔ CONFIG_6 is ×2)
   → scale, then `snap_even`, log at INFO.
3. Otherwise → log an ERROR and use the row unscaled. Across models the aspect
   ratio differs; guessing would be worse than being loudly wrong.

---

## `roi.py` — public API

Everything ROI-related lives here, per request. No `roi_config.py`.

```python
# called by capture_runner (per run) and by the arbiter's viewer screen
load_active(frame_w, frame_h, model) -> dict[str, Rect]   # name -> rect.Rect
load_defaults(...)                   -> dict[str, Rect]
save(name, rect, remark=None)        -> int    # deactivate old, insert new active
restore(name)                        -> int    # copy the isdefault row to a new active row
seed_defaults()                      -> int    # one-time: write the table above

# coordinate converters — one named function per conversion, no bare //2
to_slices(r)                     -> (yslice, xslice)  # for rgb[…] and bayer[…]
to_layer_rect(r)                 -> Rect              # full-frame layers
to_layer_rect_within(r, bound)   -> Rect              # layers of a bound crop
snap_even(r)                     -> Rect              # enforce Bayer phase
is_bayer_safe(r)                 -> bool
```

`Rect` is **`rect.Rect`** — it already carries `name` and `color`, and its
`xyslice()` already returns `(y-slice, x-slice)` in the correct numpy order.

### Cleanups this requires first  ✅ done 2026-07-31

Three things in `roi.py` blocked it from being imported by the UI or the
capture path:

1. **matplotlib and cv2 are imported at module top.** Importing `roi.py` from
   the arbiter or `capture_runner` would add a multi-second matplotlib import
   to Pi startup and make the UI depend on it. Move both inside
   `register_colorcard()`.
2. **It defines a third `Rect` class** (after `geometry.Rect` and `rect.Rect`),
   and that class's `xyslice()` is broken: `slice(self.x, self.x2)` uses the
   *method object*, not `self.x2()`. Delete `roi.Rect`, `roi.Roi`, and
   `roi.Roi_group`; use `rect.Rect`.
3. **Dead code at `roi.py:155-184`** — a `rect_to_slices` body that lost its
   `def` line and now sits unreachable after the `return` in
   `_filter_margin_rectangles`, referencing undefined names (`X`, `Y`,
   `scale_x`, `scale_y`). Confirmed by AST walk. Remove it.

`register_colorcard()` and the margin-filter helper stay as they are.

### What was done

All three cleanups applied, plus:

- **matplotlib was imported but never used** — deleted outright rather than
  moved. `cv2` and `BayerCam` moved inside `register_colorcard()`. Importing
  `roi.py` now costs **82 ms** and pulls in no cv2, matplotlib, picamera2 or
  `bayer_cam`.
- Switched from `logging_manager` to stdlib `logging` (15 `location=` kwargs
  stripped mechanically, diff reviewed line by line). That matches every
  consumer — `arbiter`, `capture_runner`, `pool`, `widgets`, `sample_repo`,
  `exposure_repo` all use stdlib logging, so roi.py's output now lands in the
  same stream at the same levels instead of `logging_manager`'s separate file.
- `register_colorcard()` gained a `pattern="BGGR"` argument. It was calling
  `extract_bayer_layers(bayer_array)` with no pattern, silently taking the
  default; now the caller can pass `cam.bayer_pattern`. Default is unchanged,
  so `test/test_roi.py` still passes and still finds the same 35 rectangles.
- The `roi` table API and the converters are implemented as specified, and
  `contains_rect(outer, inner)` was added — `save()` validation and
  `to_layer_rect_within()` both need it.
- `.role` rides along on the returned `Rect` (`'sample'`, `'white'`, `'black'`,
  `'bound'`), so the dict-of-Rect return shape is unchanged.

Verified by a `__main__` self-test on a throwaway copy of the live DB, as
`exposure_repo` does — 66 checks, all passing. The load-bearing ones:

- **Trap C is tested with real arrays, not arithmetic.** A frame is filled at
  the sample ROI, then read two ways: whole-frame layers via `to_layer_rect`,
  and layers of a `bound` crop via `to_layer_rect_within`. Both sums match.
  The test also asserts that using the *absolute* rect against the crop's
  layers yields **size 0** — the silent failure this module exists to prevent.
- `to_slices` returns (row, col) and cuts exactly 68×68×7 out of a real array.
- `snap_even` never extends past the original's right/bottom edge.
- 34×34 = 1156 samples per layer for the 68×68 sample ROI, checked against
  `.size`.
- `save` snaps, supersedes, keeps history, carries `role`/frame/model forward,
  leaves other ROIs and the isdefault row alone, and returns 0 for an unknown
  name. `restore` does not consume the default row.
- `SEED_ROIS` is asserted equal to what `db_DDL.sql` actually seeded.
- Frame-size handling: same size verbatim, same model ×2 scales and stays
  even, cross-model left unscaled.

---

## `db_DDL.sql` — complete and seed it  ✅ done 2026-07-31

The file created 6 tables and nothing else. Defects found:

1. **Three live tables are missing**: `colorcard`, `data`, `exposure`.
2. **`defaults` lacks the live `exposuregroup` column**, which
   `sample_repo.load_default_exposuregroup()` / `save_default_exposuregroup()`
   read and write.
3. **Lines 127-158 are inside a `/* … */` block**, so all five seed INSERTs and
   the `default_gui_vals` view never execute. `sample_repo` depends on that
   view, so a database built from this DDL cannot run the app. This is why the
   commissioning notes say to copy the sqlite file instead.
4. Inside that block, `CREATE VIEW` is *additionally* commented out with `--`
   (line 145).
5. The `defaults` INSERT names `samp_no_prefix`; the column is
   `samp_no_template`. Its value is `'ZZ'`; the live row holds `'ZZ1000'`.
6. The view uses `CONCAT()`, which SQLite lacks; the live view uses `||`.
7. Every table is `DROP TABLE IF EXISTS` followed by
   `CREATE TABLE IF NOT EXISTS` — the DROP makes the IF NOT EXISTS pointless
   and makes the whole script destructive.

### What was done

`src/db_DDL.sql` now defines all ten tables (the nine live ones plus `roi`),
the `default_gui_vals` view, the `ix_roi_name_active` index, and the seed rows
for `device`, `users`, `testkit`, `testlot`, `defaults` and `roi`. `samples`,
`exposure`, `colorcard` and `data` are seeded empty — they hold run data,
calibration results and card geometry, all produced by the application.

Re-run policy: **idempotent**. Every object is `CREATE … IF NOT EXISTS`, every
seed row is `INSERT OR IGNORE` with an explicit rowid, and `PRAGMA
foreign_keys = ON` makes the seed order actually checked. The destructive drop
script lives in its own file, **`src/db_DDL_reset.sql`**, so rebuilding the
schema can never erase data by accident.

Also removed: `db_manager.TABLE_DEFINITIONS`, the third (dead, unused, and
syntactically invalid — bare `CONSTRAINT UNIQUE (…)`) copy of the schema. It
named a `meterconfig` table that does not exist in the live DB.

Verification:

- Built twice into a scratch file — second run is a clean no-op.
- `PRAGMA integrity_check` and `PRAGMA foreign_key_check` both clean.
- Column-by-column diff of `pragma_table_info` / `foreign_key_list` /
  `index_list` against the live DB for all 9 pre-existing tables and the view:
  **identical**. `default_gui_vals` returns the same row.
- `sample_repo.py`, `exposure_repo.py` and `phases.py` self-tests pass against
  a DB built only from the DDL, and `main.py` boots on it (81 widgets, no
  errors) — the acceptance criterion for this step.
- Applied to the live `src/colorimeter.sqlite` (backup at
  `colorimeter.sqlite.bak-2026-07-31`). A full `.dump` diff before/after shows
  the *only* changes are the `roi` table, its 10 seed rows, and its index;
  the 10 `samples` and 35 `colorcard` rows are untouched.

Three commissioning docs said "do not build the DB from `db_DDL.sql`" and have
been corrected: `docs/RPi_commissioning.md` (twice),
`docs/commissioning/03_upload_firmware_db_venv.md`, and
`docs/commissioning/07_troubleshooting.md`. The last of these also claimed the
app errors on an empty `colorcard`; it does not read `colorcard` at runtime at
all, so that bullet was replaced with the tables the app genuinely requires.

---

## `capture_runner` — ROIs from the table  ✅ done 2026-07-31

The four module constants at `capture_runner.py:94-97` are gone. ROIs are
loaded once per run by `_load_rois(cam)`, called from `_run()` **after**
`setup_camera()` so the camera's real stream size and model can be passed to
`roi.load_active()`. The loaded dict lives on `runner.rois`, so an edit made in
the viewer takes effect on the next run with no restart.

`_load_rois` self-heals and validates:

1. Table empty (a DB predating the `roi` table) → `roi.seed_defaults()`, re-read.
2. A required ROI still missing → `FALLBACK_ROIS`, logged as an error. A
   capture that cannot find its ROIs should still produce an archive.
3. Every ROI checked with `is_bayer_safe`; containment in `bound` checked.

`FALLBACK_ROIS` and `REQUIRED_ROIS = ('sample', 'white', 'bound')` replace the
old constants. `black1`/`black2` are loaded but still not read by the capture
path, as before.

The `//2` arithmetic is gone from this module. `_channel_roi()` is deleted;
`_roi_pixels`, `_channel_means`, `_select_target_channel`, `_check_saturated`
and `_check_saturated_any` now take a `Rect` already in **layer** coordinates,
and `_run()` converts once with `roi.to_layer_rect()` — named, so trap C's two
cases can no longer be confused at a call site. `_roi_bound_layers()` takes the
bound rect and crops with `roi.to_slices()`.

### Finding: `white` was not inside `bound` — fixed

The containment check found a real pre-existing data problem on its first run,
and `bound` has since been widened from `w = 160` to `w = 196` to fix it.

| ROI | x range | y range | inside bound (w=160) | inside bound (w=196) |
|---|---|---|---|---|
| bound | 1090 – 1250 → **1286** | 360 – 990 | — | — |
| sample | 1150 – 1218 | 790 – 858 | ✅ | ✅ |
| **white** | **1150 – 1286** | 496 – 564 | ❌ **over right edge by 36 px** | ✅ |
| black1 | 1150 – 1218 | 380 – 448 | ✅ | ✅ |
| black2 | 1150 – 1218 | 900 – 968 | ✅ | ✅ |

36 px was **26 % of the white patch's width**. It cost the *run* nothing —
exposure calibration measures white against whole-frame layers, which hold the
full patch — but it cost the *archive*, which stores only the bound crop, so
re-measuring white from a stored archive used a 26 %-truncated patch. **Every
archive captured before 2026-07-31 has this truncation.**

`w = 196` is the smallest even width that contains white (1286 − 1090). The
archived crop shape changes **315 × 80 → 315 × 98**, so archives written before
and after this date differ in shape; anything reading both must take the shape
from the file rather than assume it.

The correction was applied in four places, because a commissioning error is not
a user edit and must not survive a `restore()` or a fresh DB build:

| Where | Change |
|---|---|
| `db_DDL.sql` seed | both `bound` rows, w 160 → 196, with the reason in a comment |
| `roi.SEED_ROIS` | same, so `seed_defaults()` agrees with the DDL |
| `capture_runner.FALLBACK_ROIS` | same |
| live DB | `isdefault` row corrected in place with a dated remark; active row superseded via `roi.save()`, so w=160 stays as history (rowid 10) |

Containment is still checked every run — `error` for `sample`, whose loss would
make a reading unrecoverable, `warning` for reference patches — and guarded by
`test_every_roi_lies_inside_bound`, which now covers all four ROIs and carries
the history in its docstring.

### Verification

- 53 tests across `test_capture_runner` and `test_capture_session`: OK, with
  the one documented expected failure. `test_roi_bound_crop_shape` now checks
  the bound the run actually loaded rather than a module constant.
- Two new tests: every required ROI is Bayer-safe, and `sample` is inside
  `bound`.
- **End-to-end**, on a throwaway DB copy: a baseline run archives a
  (315, 80) crop; `roi.save()` on `sample` and `bound` (exactly what the
  viewer's Save will call) then makes the next run use the new geometry and
  archive a (315, 100) crop; `roi.restore()` returns both to (315, 80). That
  closes the loop this whole design exists to open.

---

## Screen layout  ✅ built 2026-07-31

Canvas 200 × 240 — taller than wide, matching the sample rectangle, and it
gives the sample ROI plus margin a well-filled square-ish view.

```
        0                200      240      280   320
      0 ┌─────────────────┬────────┬────────┬────────┐
        │                 │   +    │   ▲    │   −    │  40
     40 │                 ├────────┼────────┼────────┤
        │                 │   ◀    │  Fit   │   ▶    │  40
     80 │                 ├────────┼────────┼────────┤
        │   canvas        │  1px   │   ▼    │  Img   │  40
    120 │   200 × 240     ├────────┴────────┴────────┤
        │                 │      < roi name >        │  26
    146 │                 ├────────────┬─────────────┤
        │                 │    Edit    │    Save     │  47
    193 │                 ├────────────┼─────────────┤
        │                 │   Cancel   │    Exit     │  47
    240 └─────────────────┴────────────┴─────────────┘
```

- 3 × 3 pad: cells 40 × 40 at x = 200/240/280, y = 0/40/80. `Fit` at the centre
  of the pad is "home"; `1px` toggles the edit step to `10px`; `Img` reimages.
- Name strip `Rect(200, 120, 120, 26)`: shows the selected ROI's name, blank
  when nothing is selected.
- 2 × 2 grid: cells 60 × 47 at x = 200/260, y = 146/193.
- DejaVuSans has ▲ ▼ ◀ ▶ — verified against the font the driver loads.

Scales, for reference:

- Whole frame 2304 × 1296 fitted → 200 × **112.5**, letterboxed with **63.75** px
  bars. (An earlier draft said 113 and 63; the exact values are 1296 × 200/2304
  = 112.5, so the frame does not land on a whole number of canvas rows at fit.)
  The fit view is for orientation only; working views are zoomed.
- Sample ROI 68 × 68 + 20 % margin → view box 81.6 × 81.6 → scale 2.45, so the
  ROI renders about 167 × 167 in the canvas.

### What was built (step 3)

| File | Added |
|---|---|
| `layout.py` | the 15 rects above, `IMG_CANVAS` … `IMG_EXIT`, with the diagram |
| `palette.py` | `roi_idle` cyan, `roi_selected` yellow, `roi_edit` green, `roi_edge` magenta; plus `unpack565()` and `rgb888()` |
| `widgets.py` | `ImageWidget` — holds a rect-sized image, blits at the rect origin |
| `pool.py` | the 15 widget instances (`img_canvas` … `img_exit`); 81 widgets → 96 |
| `mock/ili9341_mock.py` | `draw_image()` implemented — was a `log.warning` stub |

`rgb888()` goes through the same `PALETTE` as `color()`, so the names stay the
one source of colour truth even though the overlays are drawn in PIL rather
than by the widget layer. `unpack565()` replicates the low bits from the high
ones (0x1F → 255, not 248), so "white" is really white.

`ImageWidget` is deliberately a holder, not a renderer: no scaling, no
cropping, no compositing. Whatever produced the image owns the view maths. It
paints a flat background until an image is set, so a stale frame from a
previous visit never lingers.

The mock's `draw_image()` matches the hardware contract — no scaling, clipped
to the display — using `extent=(x0, x1, y1, y0)` against the mock's inverted
y-axis. **This is what makes simulation mode the desktop tool**: the same
composed canvas now renders in matplotlib and on the panel.

Verified by the `pool.py` self-test, which now:

- renders the viewer screen with and without a composed canvas;
- asserts the 13 controls **tile the right column exactly** (`covered == column`),
  none overlapping the canvas, none running off screen, none overlapping each
  other, and every button at least 40 × 40;
- presses the centre of each of the 13 and asserts it routes to itself;
- checks the four ROI colours are present and mutually distinct.

Two things checked outside the self-test:

- Rendered through the actual matplotlib mock and dumped to PNG: canvas
  blitted at (0, 0) at 200 × 240, all four overlay colours legible, name strip
  and both button grids correct. Oversized blits clip to the screen
  (extent 200–320 × 150–240); fully off-screen blits are dropped.
- **The arrow glyphs really exist in the panel's font.** `▲ ▼ ◀ ▶ − +` all
  render non-empty from `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf`,
  which is what `ILI9341._default_font_path()` picks first — so no tofu boxes
  on the hardware.

Note `IMG_FIT` overlaps `RUN_BTN`, and the viewer occupies the whole screen
with no header or footer. That is fine — the viewer is a screen of its own,
like Menu and Config, never shown alongside a slot phase.

---

## Module structure

| Module | Role | May import |
|---|---|---|
| `roi.py` | ROI domain: table, load/save/restore, converters, validation | `db_manager`, `rect` |
| `image_view.py` | pure view model: view box, zoom/pan, compose the canvas | **numpy + PIL + `rect`** |
| `widgets.ImageWidget` | holds a canvas-sized PIL image, blits at rect origin | as widgets.py does |
| `layout.py` | the rects above | — |
| `pool.py` | widget instances | — |
| `palette.py` | 4 ROI colours + `rgb888()` | — |
| `arbiter.py` | the screen: state, handlers, camera, entry/exit | everything |
| `mock/ili9341_mock.py` | implement `draw_image` (currently a stub) | — |

The import restriction on `image_view.py` is the whole point: no `ili9341`, no
`widgets`, no `pool`, no DB. It takes a numpy frame and a list of
`(Rect, rgb_tuple)` and returns a PIL image. Consequences:

- **Simulation mode becomes the desktop tool for free** — the matplotlib mock
  display renders the same canvas, once `ili9341_mock.draw_image` is
  implemented (`mock/ili9341_mock.py:241` is a `log.warning` stub today).
- A Flask front end later replaces `layout.py` + `pool.py` and keeps
  everything else — which is the doctrine already stated at the top of
  `layout.py`.
- `python3 src/image_view.py <frame>` can dump a PNG for offline checking.

### `image_view.py` API

```python
# called by the arbiter's viewer screen, and by the module's own __main__
class ViewState:
    def fit()                        # view box = whole frame  (Menu entry, Fit button)
    def fit_to(rect, margin=0.2)     # view box = rect + margin (View Sample entry)
    def zoom(factor)                 # clamped to [fit_scale, MAX_SCALE]
    def pan(dx, dy)                  # in image px, clamped to the frame
    def centre_on(rect_or_edge)      # used by edit-mode follow

def compose(frame, view, overlays, canvas_size) -> PIL.Image
    # slice frame[view.yslice, view.xslice] -> scale to canvas_size
    # (NEAREST when magnifying, so ROI edges stay honest; BILINEAR down)
    # -> stroke each overlay outline in its colour -> return canvas-sized image
```

**The frame array is never cropped or downsampled in storage.** The whole
captured frame (2304 × 1296 × 3 ≈ 9 MB) is held for the life of the screen, and
every repaint slices it. A rectangle can therefore always be enlarged — the
pixels behind the growth are already there, out to the frame edge. The PIL
image handed to `ImageWidget` is exactly canvas-sized only because
`ILI9341.draw_image` blits 1:1 with no scaling; it is a product of the slice,
not a store.

Overlays are drawn into the PIL image rather than as `RectWidget`s: clipping at
the canvas edge is free (a rectangle you are zoomed inside of clips correctly
instead of painting over the button column), and it is one SPI pass instead of
five. `compose()` neither knows nor cares where the rectangles came from, so
auto-detected rectangles are a drop-in later.

### What was built (step 5)  ✅ 2026-07-31

`rect.Rect` is imported — it is a dependency-free value type, and inventing a
fourth rectangle class to honour a "numpy + PIL only" rule would undo step 1.
Nothing else: verified at runtime that importing `image_view` pulls in no
`db_manager`, `sqlite3`, `ili9341`, `widgets`, `pool`, `picamera2`, `cv2` or
`matplotlib`.

**One invariant carries the design: the view box always has the canvas's aspect
ratio.** Letterboxing then stops being a code path and becomes a consequence —
the fit view box is 2304 × 2765 over a 2304 × 1296 frame, and `compose()` fills
whatever falls outside the frame with the background. There is no special case
anywhere.

`ViewState` stores **centre + scale**, not a rectangle, so repeated zooming
cannot drift; the box is derived on demand. Clamping: scale in
[`fit_scale`, `MAX_SCALE = 8`]; on an axis where the view is smaller than the
frame it is kept wholly inside (no panning into empty space); on an axis where
it is larger it is centred (bars stay even, panning that axis is a no-op).

`centre_on_edge(rect, side)` is the edit-mode zoom-follow, ready for step 8.

Channel order is explicit: `channel_order="BGR"` by default, because that is
what `capture_frame()` returns. Getting it wrong turns the sample blue, so it
is a named argument rather than an assumption.

Resampling is chosen by direction — NEAREST when magnifying, so a ROI edge
lands on an honest pixel boundary; BOX (area average) when minifying, because
nearest-neighbour at 0.087× would sample one pixel in twelve and could miss a
68 px ROI entirely.

Overlay outlines are stroked **one canvas pixel outside** each rectangle, so
every pixel the ROI actually covers stays visible and judgeable.

#### Fit-view cost

A full BOX average at fit reads all 3 M pixels of the frame to make 22 K, and
that measured **12.46 ms on the desktop** — likely ~100 ms+ on a Zero, the
slowest thing the viewer would do. `compose()` now pre-thins with a strided
numpy view when `s < 0.5`, keeping ≥ 2× oversampling for BOX to average over:

| view | before | after |
|---|---|---|
| fit (minify) | 12.46 ms | **0.66 ms** |
| sample (magnify) | 0.07 ms | 0.07 ms |

Guarded by a test that a 68 × 68 block still survives at fit zoom (it covers 42
canvas px) and lands where the transform says — striding must not alias a
ROI-sized feature away.

#### Self-test

`python3 src/image_view.py [frame.jpg|.npy] [out.png]` — 60+ checks, then a
three-panel PNG (fit / sample +20 % / bound) with the ROIs from the database
drawn on. Notable checks:

- the view box keeps the canvas aspect through every zoom;
- zoom clamps both ways, pan clamps to all four frame edges, the letterboxed
  axis stays centred and will not pan;
- `canvas_to_image` and `image_to_canvas` are **exact inverses** to 1e-9 — this
  is what turns a touch into a ROI hit-test, so an approximation would not do;
- a single lit image pixel composes to the canvas position the transform
  predicts (centroid within 1.5 px);
- the outline sits one px outside the ROI and the first ROI pixel is *not*
  overdrawn;
- bad input is refused rather than guessed: 2-D frames, 4-channel frames, a
  frame whose size disagrees with the view, an unknown channel order.

---

---

## Finding: the `white` ROI is half off its patch

Step 5's whole purpose is checking ROI geometry on a desktop before the Pi, and
the first real capture it was pointed at showed a problem.

Measured on `dev/captures/20260615_2120/camtest8_white_e3.jpg` (2304 × 1296,
white LED), mean RGB and luminance spread inside each stored ROI:

| ROI | mean lum | std | CV | verdict |
|---|---|---|---|---|
| sample | 124.7 | 1.7 | **1.4 %** | uniform — well placed |
| black1 | 5.3 | 2.6 | — | near-black, correct |
| black2 | 4.9 | 2.7 | — | near-black, correct |
| **white** | **97.1** | **81.2** | **83.6 %** | **straddles an edge** |

A luminance profile across the white ROI shows why. The bright patch runs
x ≈ 1121–1226; the stored ROI is x 1150–1286. It falls off the right-hand end
of the patch:

```
x=1146  lum=126.7  #####################
x=1160  lum=174.7  #############################  <-- ROI
x=1216  lum=171.3  ############################   <-- ROI
x=1230  lum=  9.7  #                              <-- ROI   (off the patch)
x=1272  lum=  6.0  #                              <-- ROI
```

**Only 56 % of the white ROI is on the patch** (76 of 136 px in x). The other
44 % is dark background, which has been dragging the white reference mean down
on every capture. Vertically it is fine — the patch is 129 px tall and the ROI's
68 px sit inside it.

For scale: a 10 %-inset ROI on the measured patch would be
`(1130, 506, 84, 104)`, reading mean 162.7 with CV 13.3 % instead of 83.6 %, and
it fits inside the widened `bound`.

**Not changed.** This is one capture from 2026-06-15 and the staging may have
moved since; the number to trust is one measured on the current instrument,
which is exactly what these tools are for. Worth checking as soon as Tool 1
runs.

How much it matters today is limited but not nil: saturation detection uses
p90, and with ~56 % of pixels bright the p90 still lands on the patch, so
exposure calibration has not been badly misled. The white *mean* — used by
`_select_target_channel` — is roughly halved.

---

## What was built (step 6) — Tool 1  ✅ 2026-07-31

`Menu → View Image` (the old `stub1`) opens the viewer. Exit returns to the
Menu, so the menu's OK/CANCEL still get their say over anything staged before
it was opened; `open_image_view(slot=...)` returns to a slot instead, which is
Tool 2's path in step 7.

New arbiter surface: `preview_frame()`, `_preview_exposure_us()`,
`open_image_view()`, `_load_view_rois()`, `_view_reimage()`,
`_render_image_view()`, `_paint_canvas()`, `_repaint_canvas()`, and the
handlers. State is eight fields, all cleared on exit — `_view_frame` is ~9 MB,
so it is explicitly dropped rather than left for the next open to replace.

`ImageWidget` gained `on_press(cx, cy)`, called with coordinates **relative to
the canvas**, not the screen. Screen coordinates would make every caller
subtract the rect origin before unprojecting, and getting that wrong is
invisible until a ROI selects from the wrong place.

Pan moves a **quarter of the visible box**, not a fixed pixel count, so one
press moves the same proportion of the screen at every zoom: a big jump at fit,
a nudge when zoomed to an edge. Zoom is ×1.5 per press.

`Edit`/`Save`/`Cancel` are shown disabled — the grid reads as a whole while
step 8 is outstanding, exactly as the menu stubs did.

### Verification

45 checks added to `arbiter.py --selftest`, over a fake camera that returns a
synthetic frame and a fake LED bank that records every switch:

- the camera guard: viewer alone keeps `_camera_wanted()` true with both slots
  idle, and `_maybe_release_camera()` respects it;
- opening takes exactly one frame, cycles the white LED on then off, loads all
  five ROIs, fits to the frame, and shows all 15 widgets;
- zoom in/out/clamp, pan and return, Fit;
- **the ambiguity rule**: a tap inside `sample` (which is also inside `bound`)
  selects `sample`; tapping again gives `bound`; again gives `sample` — the
  two-way toggle, working through the real touch path;
- a tap on empty space deselects; the 1px/10px toggle; reimage takes a fresh
  frame and re-cycles the LED;
- Exit closes, frees the frame, releases the camera claim, returns to the Menu;
- **capture lockout**: with a capture in flight, `View Image` is disabled and
  `open_image_view()` refuses without taking a frame;
- a failed preview still opens the screen, clears the canvas, and puts the
  reason in the name strip.

Then run for real in simulation against the prototype capture: the fit view
shows the whole frame letterboxed with all five ROIs; selecting the sample
fills the name strip with `sample 68x68`; zooming to the white ROI shows —
plainly, by eye — the overhang that step 5 measured, the selected rectangle
running well off the bright patch into dark background.

---

## Interaction

### View mode

| Control | Effect |
|---|---|
| ▲ ▼ ◀ ▶ | pan the view |
| `+` `−` | zoom, clamped between fit and MAX_SCALE |
| `Fit` | whole frame |
| `Img` | reimage (fresh white-LED exposure) |
| tap inside a rectangle | select it; its name shows in the name strip |
| `Edit` | enabled only when a rectangle is selected |
| `Exit` | leave the screen |

**Selection is single.** `selected` holds one name or `None`, not a set —
selecting a rectangle deselects whatever was selected before. The name strip
shows one name, and `Edit` needs exactly one, so a set bought nothing.

**Ambiguous taps.** A tap can land inside several rectangles at once: `bound`
(160 × 630) contains `sample` (68 × 68), and when zoomed out several rectangles
may be only a few screen pixels apart. Rather than trying to guess the intended
one, **just pick one — preferring one that is not currently selected**, so
tapping the same spot again moves on to the next. With two overlapping
rectangles that is a plain toggle between them.

The rule, given the candidates that contain the tap:

1. No candidate → deselect; the name strip clears.
2. Exactly one candidate, and it is already selected → deselect it. (This keeps
   the earlier "tap toggles" behaviour for a lone rectangle — without it a
   single rectangle could never be deselected by touch.)
3. Otherwise → order the candidates deterministically, start scanning just
   **after** the currently selected one, wrap around, and take the first hit.
   That is the first candidate that is not currently selected, and it advances
   by one on each repeat tap.

The ordering is **by area, smallest first**, so the first tap on overlapping
rectangles lands on the tightest one — usually `sample` rather than `bound`,
which is what a finger aiming at the sample intends. Ties break by name so the
order is stable across repaints.

Scanning from *after* the current selection rather than from the start is what
makes step 3 cycle through **all** the candidates instead of bouncing between
the first two. For the two-rectangle case the user described, it reduces
exactly to the toggle: A → B → A → B.

### Edit mode

Four palette colours, as specified: `roi_idle` (not selected), `roi_selected`
(selected, not being edited), `roi_edit` (the rectangle under edit), `roi_edge`
(its armed side). Overlays are drawn in PIL, so `palette.rgb888(name)` unpacks
RGB565 — names stay the single source of truth.

State: `mode` (view | edit), `selected` (one name or `None`), `editing` (a
working copy), `armed_side` (None | left | right | top | bottom), `step`
(1 | 10 image px).

| Gesture | Effect |
|---|---|
| tap near a side of the edited rectangle | toggle that side armed; one at a time |
| ▲ ▼ ◀ ▶ with a side armed | move that edge by `step` |
| ▲ ▼ ◀ ▶ with no side armed | move the whole rectangle by `step` |
| `+` `−` | still zoom the view |
| `Save` | validate, write, reload, back to view mode |
| `Cancel` | discard the working copy, back to view mode |

No ROI toggle button is needed — selection is by touch.

**Zoom follow.** Pressing `Edit` zooms to `fit_to(rect, 0.5)`. Arming a side
re-centres the view on **that edge**, and subsequent nudges keep the edge
centred; with no side armed, the view keeps the whole rectangle in frame. This
also removes the edge-tapping problem: edge tolerance is a screen-pixel band
converted to image pixels (`tol / scale`), and at fit zoom the sample is about
6 screen pixels wide, where every tap would count as an edge. Auto-zooming on
`Edit` means edges are never tapped at a useless scale.

**`Exit` is disabled while editing** — Save or Cancel first. That avoids a
lost-work modal entirely.

### Save validation

1. `snap_even()` — **the one that fails silently if skipped.** Odd coordinates
   shift the Bayer phase (Traps A and B above) and change which of B/Gb/Gr/R is
   actually being sampled.
2. Clamp inside the frame.
3. `sample` must stay inside `bound` — analysis locates the sample relative to
   the bound crop.
4. Minimum size 16 × 16.

## What was built (step 8) — edit mode  ✅ 2026-07-31

Three pieces of state: `_view_mode` (view | edit), `_view_editing` (a working
copy — the stored ROI is untouched until Save, so Cancel is free) and
`_view_armed`.

| Gesture | Effect |
|---|---|
| `Edit` | working copy + `fit_to(rect, 0.5)`; Save/Cancel enable, Edit and **Exit** disable |
| tap within 12 canvas px of a side | arm it; tap it again, or anywhere else, to disarm |
| ▲ ▼ ◀ ▶ with a side armed | move that edge by the step; a perpendicular arrow does nothing |
| ▲ ▼ ◀ ▶ with none armed | move the whole rectangle |
| `1px` / `10px` | the step |
| `+` `−` `Fit` `Img` | still zoom / refit / reimage |
| `Save` | validate → `roi.save()` → reload → view mode |
| `Cancel` | discard → view mode |

The arrows do double duty — pan in view mode, nudge in edit mode — so the same
four buttons cover both without a mode button.

**Zoom-follow.** Arming an edge centres the view on it, and every nudge
re-centres, so the edge being moved stays under the eye. Disarming re-centres
on the whole rectangle: without that, the view stays parked on the old edge
with the opposite one off canvas, and the only way to reach it would be to zoom
out. That was found by the self-test failing on exactly that sequence.

The edge tolerance is a screen band (12 canvas px) converted to image pixels,
so it stays a finger's width at every zoom. Auto-zooming on `Edit` is what
makes it workable — at fit zoom the sample ROI is about six canvas pixels wide
and every tap would be within tolerance of every edge.

**The armed side is drawn as a degenerate rectangle** — zero width for a
vertical edge, zero height for a horizontal one. `compose` strokes its outline
just outside the bounds, so a zero-width rect comes out as a two-pixel line,
and the compositor needs to know nothing about edit mode.

**Exit is withheld while editing**, so there is no way to walk away from an
unsaved working copy and therefore no lost-work modal to write.

A rejected Save leaves edit mode running with the reason in the name strip
(`min 16px`, `outside frame`, `sample outside bound`), so the edit can be
corrected rather than lost.

### Also fixed here

`Img` no longer throws away the view. Reimaging used to rebuild the ViewState
from scratch; it now keeps the scale and centre when the stream size is
unchanged, which matters most mid-edit — the point of reimaging there is to see
a moved edge under a fresh exposure, not to start over.

`pool.on()` / `pool.put(on=...)` now accept any widget carrying `on_press`,
not just `Button`, so the canvas is wired like every other widget instead of
reaching into `pool["img_canvas"].on_press`.

### Verification

46 checks in `arbiter.py --selftest`, against the scratch DB copy so real
`roi.save()` / `roi.restore()` run without touching the live database:

- Edit takes a working copy, leaves the stored ROI alone, zooms to ×1.96
  (= 200 / (68 × 1.5)), and flips all four buttons; **`Exit` really refuses**
  while editing, not merely renders disabled;
- arrows move the whole rect and return it exactly;
- a tap arms the near side, the view centres on it, the name strip reads
  `left 68x68`, tapping again disarms — and the opposite edge is then reachable;
- an armed edge moves alone, and a perpendicular arrow does nothing;
- 200 shrink presses stop at the 16 px minimum; 500 presses toward the corner
  stop at (0, 0);
- Cancel discards with the stored ROI unchanged;
- all three validation refusals stay in edit mode and write nothing;
- Save snaps `(1151, 791, 41, 41)` → `(1150, 790, 40, 40)`, stores it, reloads
  the screen, and **keeps the superseded row** rather than overwriting;
- `restore()` puts the commissioned default back;
- reimaging keeps scale and centre.

Then run for real in simulation: arming the right edge and nudging it three
times takes the sample from 68 → 98 px wide, Save stores it, and the table
holds 3 rows for `sample` with 1 active. All four colours are distinguishable
on the screenshot — green rect under edit, magenta armed edge, cyan idle
`bound`.

### Simulation gap left open

The mock camera reports **imx219** while serving a 2304 × 1296 imx708 capture,
so `roi.load_active()` logs a cross-model mismatch for every ROI in
simulation. The warning is correct and worth leaving loud — the geometry
really is being applied to a stream it was not defined against.

Reporting imx708 instead does not work yet: that profile's raw format is
packed (`SBGGR10_CSI2P`), and the mock synthesizes an *unpacked* uint16 array
when the served image has no `.npy`, so `unpack_raw10()` fails on the reshape.
Making the mock serve packed raw fixes both, and is the right fix when the mock
is next worked on. `$COLORIMETER_MOCK_MODEL` overrides in the meantime, and the
mock's `libcamera` stub now carries `AfModeEnum` / `AfTriggerEnum` so the
autofocus path is at least reachable.

---

## Illumination and camera ownership  ✅ built 2026-07-31

`preview_frame()`: white LED on (channel 10) → **0.25 s** settle → one frame →
LED off, in a `finally` so the LED cannot be left burning by an exception.
Roughly 0.5 s total. The capture path's `LED_ON_WARMUP_S = 2.0` is for
photometric accuracy, which this does not need.

- Borrows the arbiter's persistent camera via `_ensure_camera()`; never opens
  its own. Returns `(frame, error)` — a failed preview leaves the screen up
  with the reason in the name strip rather than refusing to open.
- **Exposure comes from the `exposure` table, not a new config value.**
  `_preview_exposure_us()` takes the middle rung of whatever active group the
  white LED has (trying '1', '3', '5'), falling back to 40 000 µs. That way the
  preview looks like what a capture of the same scene would see, with no new
  knob to keep in step. Restoring the previous exposure afterwards proved
  unnecessary: `capture_runner` sets its own exposure at the start of every
  run.
- ⚠️ **`_camera_wanted()` includes `self._view_open`.** Without it
  `_maybe_release_camera()` closes the camera on the very next main-loop pass
  (no slot is in a `_CAMERA_PHASES` phase when the viewer is opened from the
  Menu) and every `Img` press pays a fresh sensor open. Covered by a self-test
  that asserts the viewer alone keeps the camera wanted with both slots idle.
- The viewer is a non-slot screen: `_focused = None`, like the Menu. That is
  what keeps stray repaints (`_tick_1hz`, `_arm_alarm`) off the image — both
  already guard on `_focused is not None`.
- Reagent timers keep running and still alarm audibly while the viewer is open;
  the visible alarm arms and is picked up on return.

### The mock camera was pointing at a dead path

`picamera2_mock.MOCKIMAGEFILE` was an absolute path under an
`eclipse-workspace` directory that no longer exists, so the mock camera raised
`FileNotFoundError` on **every** capture — simulation could not have exercised
the viewer at all.

Replaced with a repo-relative candidate list, first existing one wins, and
`$COLORIMETER_MOCK_IMAGE` overrides. The first candidate is
`dev/captures/20260615_2120/camtest8_white_e3` — a real 2304 × 1296 prototype
capture under the white LED, so the mock returns a frame the **stored ROI
rectangles actually apply to**. That is what makes simulation a genuine desktop
version of Tool 1 rather than a shape-only mock.

## What was built (step 7) — Tool 2  ✅ 2026-07-31

The read phase's `Elapsed` label rect (`TIMER_LBL`) carries a **`View Sample`**
button instead, whenever no capture is running. Pressing it calls
`open_image_view(slot=slot, focus_roi="sample")` — the same screen as Tool 1,
with all its tools, opened selected and zoomed to the sample ROI plus 20 %
(scale ×2.45 on the 200-wide canvas, centred, the whole ROI on screen). `Exit`
returns to the read screen with the phase intact.

A second widget shares the rect rather than the label becoming a button: the
pool already does this wherever two screens reuse a row (`id_lbl` /
`temp_lbl`), and it keeps the label's per-phase font sizing untouched for the
reagent and analysis phases.

**From the moment CAPTURE is pressed the button is replaced by the label, not
disabled.** The user's rule, and the stronger reading is the right one — the
imaging tools share the camera and LED bank with the capture path, so there
must be nothing there to press. The condition is
`arb.capture_available() and not slot.capturing`, which covers a capture
started by either slot.

### Verification

- `phases.py` self-test: before CAPTURE the button is shown on the label's rect
  and the label is hidden; pressing it records
  `open_image_view(slot="A", focus_roi="sample")`. After CAPTURE the button is
  gone, `Elapsed` is back, and a **tap on that spot is not consumed by
  anything** — the strong form of the rule, tested through the touch path
  rather than by inspecting an `enabled` flag.
- `arbiter.py --selftest`: Tool 2 opens at ×2.45 centred on the sample with the
  whole ROI on canvas and `sample 68x68` in the name strip; `Exit` returns
  focus to the slot, still in the read phase, offering the button again.
- Run for real in simulation: the read screen shows `View Sample` where
  `Elapsed` sits, the viewer opens filled by the sample patch, and once
  capturing the screen reads `Elapsed` / `RUNNING` with no button.

---

## Capture lockout

All imaging tools are disabled from the moment CAPTURE is pressed until the
last image is captured. One guard covers it: `arb.capture_available()` is
exactly `self._capture is None`, and it is already true only between captures,
of either kind (read-phase or Menu-launched).

- `menu_stub1` ("View Image") — `enabled=self.capture_available()`.
- Read phase — `view_btn` enabled on the same call, so a capture running on
  slot B also locks slot A's viewer.
- `preview_frame()` refuses if `not capture_available()`.

On the read phase the button and the label swap: `ReadPhase.enter` already
branches on `slot.capturing`. The capturing branch shows `timer_lbl` =
"Elapsed" as it does today; the idle branch shows `view_btn` = "View Sample".
Same rect (`layout.TIMER_LBL`), so nothing else moves.

## Performance  ✅ done 2026-07-31

`ILI9341.draw_image` packed RGB565 in a per-pixel Python loop — 48 000
iterations for a 200 × 240 canvas, on every pan, zoom or reimage press.

The packing is now `pack_rgb565(img)`, a module-level function in
`ili9341.py`, vectorised with numpy and falling back to the original loop when
numpy is absent. `draw_image` shrank to three lines.

Measured **on the desktop** (`python3 src/ili9341.py`), 200 × 240 canvas:

| | per repaint |
|---|---|
| per-pixel loop | 6.92 ms |
| numpy | 0.23 ms |
| speed-up | **29.9×** |

The Pi has not been measured. The ratio there should be at least this good —
interpreted bytecode suffers more from the weaker core than vectorised C does —
so the loop was plausibly costing tens of milliseconds per repaint.

What this leaves as the floor is the SPI transfer itself: 200 × 240 × 2 bytes
= 96 000 bytes = 768 kbit, so **24 ms at 32 MHz** (Zero 2 W) or **48 ms at
16 MHz** (Zero W). Packing is now negligible beside it, which is the right
shape — further gains would have to come from transferring less, not from
computing faster.

### Testability

The pixel format is the panel's contract, so it should be checkable without a
panel. `spidev` and `gpiozero` are now guarded imports, which makes
`ili9341.py` importable on a desktop; `init()` raises a clear error naming what
is missing and pointing at `COLORIMETER_SIMULATION=1`, rather than the module
failing at import. On a Pi nothing changes.

That enables a `__main__` self-test that runs anywhere: it checks
`pack_rgb565` **byte-for-byte against the original per-pixel loop**, kept in
the test as the correctness oracle — on solid extremes (black, white, pure R/G/B,
mid grey, an odd-bit colour) and on random images at 1 × 1, 3 × 2, 200 × 240 and
320 × 240 — asserts big-endian byte order (red → `F8 00`, blue → `00 1F`),
checks non-RGB input is converted, and prints the timing above.
`ILI9341.rgb()` was separately confirmed to agree with `pack_rgb565` across the
channel range, so the two RGB565 paths in the driver cannot drift.

---

## Steps

Each step is independently verifiable.

| # | Step | Verified by |
|---|---|---|
| 0 | ✅ Complete `db_DDL.sql` (9 tables + view + seeds, defects above) and add the `roi` table | ✅ built a scratch DB from the DDL; `main.py` + 3 self-tests run against it |
| 1 | ✅ `roi.py` rework: 3 cleanups, table API, converters | ✅ 74-check self-test on a throwaway DB copy, as `exposure_repo` does |
| 2 | ✅ `capture_runner` reads ROIs from `roi.py` | ✅ 53 tests pass; end-to-end edit→run→archive check |
| 3 | ✅ `ImageWidget` + layout rects + pool + 4 palette colours + `ili9341_mock.draw_image` | ✅ `pool.py` self-test (tiling, routing, colours) + mock render to PNG |
| 4 | ✅ numpy fast path in `draw_image` | ✅ `ili9341.py` self-test: byte-identical to the old loop, 29.9× faster |
| 5 | ✅ `image_view.py` + `__main__` PNG dump | ✅ 60+ self-test checks; ROI geometry checked against a real capture — found the `white` ROI half off its patch |
| 6 | ✅ Arbiter viewer screen, `preview_frame()`, camera guards, Menu/stub1 | ✅ **Tool 1 works** — 45 arbiter self-test checks + simulation screenshots |
| 7 | ✅ Read phase "View Sample" button | ✅ **Tool 2 works** — phases + arbiter self-tests, simulation screenshots |
| 8 | ✅ Edit mode | ✅ 46 arbiter self-test checks incl. real `roi.save`/`restore`; simulation screenshots |

## Deferred

- **Long-hold acceleration.** The arbiter's touch loop
  (`arbiter.py:1469-1480`) fires a button once on press, then spins until
  release with a 1 s timeout; a hold currently just blocks. Auto-repeat needs a
  `repeat` flag on `Button` plus a hold-poll in that loop — contained, but it
  is the shared input path for every screen. The 1px/10px toggle moves a 68 px
  edge in 7 taps, which should suffice.
- **Automatic detection of target rectangles.** `compose()` takes a list of
  rectangles, so a detector supplying that list needs no change here.
- **Editing ROIs other than `sample`.** The machinery is generic; only the
  validation rules are sample-specific.
