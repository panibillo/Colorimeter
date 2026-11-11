# Desktop tools: looking inside the files, and first visual checks

Last reviewed: 2026-11-11 (archive move the same day).

This document lists the desktop scripts and functions that answer "what is in
this file?" and "does this capture look right?" before any analysis is
trusted. It covers the capture archives (`captures/*.h5`), the database
(`src/colorimeter.sqlite` and the two `devdesk` databases), contact sheets,
and the figures that show where the pad finder put the pad.

Companion documents:

- [analysis_chain_tools.md](analysis_chain_tools.md): the analysis chain, step by step.
- [pi_transfer_tools.md](pi_transfer_tools.md): moving files and data between the desktop and the Pi.

Each status below comes from reading the code, its own notes, `HOWTO.md` and
the git history on the date above. Nothing was run to confirm it.

---

## Status key

The same key is used in all three documents.

| Status | Meaning |
|---|---|
| **Current** | In use now. Use it. |
| **Engine** | Library code that current scripts import. You rarely run it on its own (some have a self-test). |
| **Open study** | A study that is still in progress. |
| **Record** | A finished study or report. Kept as the record of how a decision was made. Its results are not current; don't build on it. |
| **Superseded** | Replaced by a newer method, named in the entry. Candidate to move to an archive folder. |
| **Parked** | Working code kept for later, but not used by the current chain. |
| **Broken** | No longer runs as written. Kept only as a record. |
| **Deprecated** | Don't use. Candidate to delete. |

Run everything with the project interpreter, from the repository root, with
a time cap (see `CLAUDE.md`):

    timeout -k 10 600 .venv/bin/python3 -u <script>

---

## 1. Looking inside a capture archive (.h5)

**Purpose.** Find out what one capture actually holds before asking anything
of it: which sections (the measurement, and the pre-image under `/pre`),
which LEDs, which exposure rungs and exposure_us, which Bayer layers, which
dark frames, and whether anything is clipped.

**Method.** The scripts open the archive read-only through `src/hdf5read.py`
and print a table, or draw one LED photo as a picture. None of them change
the archive.

| File / function | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `devdesk/hdf5inspect.py` | **Current** | Command-line inspector. Add `--pre` to any mode to read the pre-image instead of the measurement. | See the modes below. |
| &nbsp;&nbsp;`-s` / `--summary` (default) | Current | One row per LED and exposure label: exposure_us, ROI shape, layers stored, JPEG size; then the dark frames and their mean per layer. | Report printed to the terminal. |
| &nbsp;&nbsp;`-j` / `--jpeg_extract` | Current | Writes each LED's embedded JPEG out as a file. | `captures/<samp_no>/<color>.jpg`, or `captures/<samp_no>/pre/<color>.jpg` with `--pre`. |
| &nbsp;&nbsp;`-r` / `--roi_plot` | Current | One LED photo as a color picture (R, Gr, B), or gray with `--gray`. The test ROI boxes are drawn on top. | Figure on screen; PNG with `-o <file>`. |
| &nbsp;&nbsp;`-r --layers` | Current | One panel per Bayer layer on a shared 0–1023 scale. The only view that shows Gb. | Figure on screen; PNG with `-o <file>`. |
| &nbsp;&nbsp;`-w` / `--write_db` | Current, rarely needed | Dark-corrected mean and standard deviation over each test ROI box, for every LED, exposure and layer. | Database rows in `devdesk/devdesk.sqlite` (tables `roi`, `bayervals`); `roi_fit.png` beside the archive when a box falls partly off the frame. |
| &nbsp;&nbsp;`section_out_dir()` | Engine | Decides the folder that extracted files go into. `analysis_1` uses it too. | — |
| `analysis_1.inventory(samp_no)` | **Current** | The same questions as `-s`, plus the gray-chip levels and the fraction of pixels clipped, for both sections. Run it first on any new capture. | Report printed to the terminal. |
| `analysis_1.rung_verdict(samp_no, cell)` | **Current** | For one channel, says which exposure rung to read and, if none, why not (for example "clipped: 30% of the pad and chips at the 1023 ceiling"). | Returns `(rung, reason)`. |
| `analysis_1.saturation()` | Engine | Fraction of a region's raw counts at or above the clipping level (1000 counts). | Returns a number. |
| `analysis_1.extract_images_from_hd5(samp_no)` | **Current** | Writes the archive's embedded images out as files, one folder per section. | `captures/<samp_no>/` and `captures/<samp_no>/pre/`. |
| `probe.py` (run directly: `probe.py C1038`) | **Current** | Prints each section's bound rectangle, ROI rectangles in layer coordinates, LEDs, rungs and exposure_us. | Report printed to the terminal. |
| `probe.Capture`, `probe.sections()` | **Engine** | Opens one section of an archive and hands back dark-corrected Bayer layers. Almost every analysis script reads archives through this. | — |
| `devdesk/roi_darkdiff.py` | **Record** | Compared dark frames taken before and after a sweep. Works only on archives from before 2026-08, which stored both. Newer archives report "no matched pairs". | Report printed to the terminal; histograms on screen. |

---

## 2. Looking inside the database

**Purpose.** Check that the rows the analysis depends on are present and in
force: the `samples` row for a capture (holder, `manual_read`
concentration), the `roi` rectangles, the published flat-field models in
`layermodels`, the `hotpixels` list, and the desktop-only `sample_roi`
geometry.

**Method.** There is no dedicated database browser script. Use the `sqlite3`
command line, or the functions below that read one table for a purpose.

| File / function | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `sqlite3 src/colorimeter.sqlite "SELECT * FROM sample_roi WHERE samp_no='C1038'"` | Current | Plain query. Swap the table and condition as needed. | Terminal. |
| `analysis_1.read_sample_roi(samp_no, sec)` | **Current** | Reads one section's stored pad geometry back out of `sample_roi`. | Returns a dict. |
| `analysis_1.models()` / `whitecorr.load_models()` | **Engine** | Reads the `layermodels` rows in force (the gray-chip flat-field polynomials). | Returns `{(led, layer): LayerModel}`. |
| `desktop/fetch_samplerows_from_pi.py --list-tables` | **Current** | Lists the tables in the Pi's database. (Copying rows is in [pi_transfer_tools.md](pi_transfer_tools.md).) | Terminal. |

Things to know:

- `sample_roi` is desktop-only. It isn't in `src/db_DDL.sql`, and
  `pushdb.sh` carries it to the Pi, where nothing reads it.
- `roi.active` holds 0, 1, 2, 3 and 9. Only 0, 1 and 2 are defined. Rows at 3
  and 9 are invisible to every loader (`HOWTO.md` §8.10).
- There are several dated database copies at the repository root and in
  `src/` (for example `rpizero3_2026-11-02.sqlite`,
  `src/colorimeter.sqlite.bak-*`). Only `src/colorimeter.sqlite` is read by
  the analysis code.

---

## 3. Finding the pad, and showing where it was found

**Purpose.** Every number in the analysis is read over the pad, so the pad
has to be found correctly first, and a person has to look at the result.
Bill's rule: if the rectangle isn't on the pad, every number after it is
void.

**Method.** The pad finder in use, `padrect.PadRect`, doesn't threshold the
pad's color, because an unexposed pad has no color step. It uses the
geometry the holder guarantees: the pad is a rectangle of fixed size, tilted
no more than about a degree. It finds the strip's two side edges and fits
them as two parallel lines. It then places the pad's bottom edge on the
shadow line where the pad stands proud of the strip, and puts the top one
fixed pad height above. Since cassette 01 (C1038 onward) it uses the
"window" holder layout and measures the sides on the pad's own rows. From
2026-11-11, for the cassette holders (`samples.cap_style` F, G1, H1),
`analysis_1.pad_of` takes the layout from `cap_style` (`CAP_LAYOUT`) instead
of guessing it from the image. It also finds the sides *guided*: each side
is the steepest drop to the dark frame within 2 px of a prior fit. The
pre-image is guided by its own first fit, and the post-image by the
pre-image. The older "half the pad level" side rule is no longer the final
side in those holders (dye at the edge made it fit wide); the last commit
before the change is 50b70a9. The
search image is `fields.composite`: each LED photo divided by the gray-chip
level, then averaged over every LED. That makes it work whichever LEDs a
capture holds.

| File / function | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `padrect.PadRect` | **Engine, current** | The pad finder. Gives the rotated rectangle, the edge fits and quality numbers (`describe()`, `mask()`, `paper_mask()`, `top_clearance`, `bot_source`). | — |
| `padrect.py` (run directly) | **Current** | One quality line per capture (width, tilt, edge-fit residual, rows rejected, top residual, clearance, edge softness), then the pooled width and height. `HOWTO.md` §3 explains each field. The capture list in `__main__` is edited by hand (currently `["C1025"]`). | Report printed to the terminal. |
| `padrect.pooled_size()`, `padrect.edge_softness()` | Current | Re-derive the fixed pad width and height for a new strip lot; measure how sharp each strip edge is. | Returned numbers. |
| `fields.composite()`, `fields.reflectance()`, `fields.chip_levels()` | **Engine** | The all-LED search image the pad finder works on. | — |
| `analysis_1.locate_pad(samp_no, write=True)` | **Current** | Fits the pad in both sections of a capture and records it. `write=False` fits without writing. | Database: one row per section in `sample_roi` (`src/colorimeter.sqlite`). Terminal report. |
| `analysis_1.pad_of()` | Engine | Cached `PadRect` for one section. For cassette cap_styles: layout from `CAP_LAYOUT`, sides guided (`describe()` ends `window/guided`). | — |
| `analysis_1.roi_overlay(samp_no)` | **Current** | **The pad-finding check figure.** The crop as measured, beside the same crop with the pad, the gray chips and the paper region drawn on. Look at it before believing any number. | Figure: `desktop/padanalysis/fig/analysis1_<samp_no>_roi.png` (or `_roi_pre.png`). |
| `analysis_1.pre_post_overlay(samp_no, led, layer)` | **Current** | The lined-up pre-image beside the post-image, and their ratio. Structure that follows the pad outline means the lining up is wrong. Prints the corner mismatch (should be well under one pixel). | Figure: `fig/analysis1_<samp_no>_prepost.png`. |
| `archive/segment.py` (`segment.Pad`) | **Superseded** by `padrect` | The earlier free-form finder: thresholds and grows a region. Needs the white LED. Kept as the comparison arm for `report7` and `report8`. | Self-test report on the terminal. |
| `archive/report7.py` | **Record**, runs | The rectangle finder against the free-form one, drawn. White-LED captures only (before 2026-08-13). | Figure: `archive/fig/padrect.png`. |
| `archive/look2.py` | **Superseded**, runs | The free-form segmentation drawn on the first five captures. | Figure: `archive/fig/look2.png`. |
| `padregistration.py` | **Parked**, see the chain document, step 4 | Lining up by edge correlation (shelved). | — |

Note: since 2026-11-11 `padrect.py` has a `minrows` argument on the side
finders and the guided side finder (`prior=`, `layout=`, `guide_sides()`,
`SIDE_SEARCH_PX`). `sample_roi` holds rows for C1016–C1025 (August, labeled
channel/half-level) and for the 28 C1038–C1045 repeat files (window/guided,
written by `locate_pad` on 2026-11-11).

---

## 4. Contact sheets and other picture views

**Purpose.** See a whole capture at once, or see one photo the way the eye
would. These figures judge other results; they aren't measurements.

**Method.** A contact sheet puts every channel (LED + Bayer layer) of one
section side by side on one shared color scale, so that light and dark
compare fairly between panels. `colorview` stacks the R, G and B layers of
one LED photo into a color picture, all divided by the same number, so the
LED's own color is kept.

| File / function | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `analysis_1.contact_sheet(samp_no, quantity=..., sec=...)` | **Current** | Every modeled channel of one section as a small panel, one shared scale. `quantity` is one of `sig`, `R`, `R_inf`, `A`, `KS`, `KS_signed`. | Figure: `fig/analysis1_<samp_no>_cells_<quantity>.png` (`_pre` added for the pre-image). |
| `analysis_1.cell_table(samp_no, roi=..., sec=...)` | **Current** | The contact sheet as numbers: one row per channel with pixel count and percentiles. On the pre-image pad it is also the arithmetic check (R_inf median must be 1.0000). | Report printed to the terminal. |
| `colorview.show_color()` / `color_image()` / `stretch()` | **Current** (helper) | One LED photo as a color picture, optionally contrast-stretched over one region. | Figure: `fig/<label>_colorview_<samp_no>_<pre|post>_<led>_<rung>.png` (label defaults to `aux`). |
| `duotone.render_rgb()` | Engine | A true-color picture of the crop from the white LED. Only on captures that stored the white LED. | Returned array. |
| `dot_study.show_cell()` | **Record** | One channel raw, beside the same with the pad drawn on (dot captures C1028–C1036). | Figure: `fig/dot_study_<samp_no>_<what>.png`. |
| `archive/look1.py` | **Superseded**, runs | First look at the layer data, first five captures. | Figure: `archive/fig/look1.png`. |
| `archive/look3.py` | **Broken** (record) | Each pad in true color beside the two-dye model's white/yellow/brown call. Stopped running when the white reference moved to the pre-image (2026-09-27). | Figure (old): `archive/fig/look3.png`. |

**Not built yet:** the work plan
(`docs/work_plan_chain_poc_2026-11-09.md`, step 0) asks for one contact
sheet per sample showing the pre-image and the post-image together, plus a
sample table (holder, arsenic, pre-image present, channels, rungs, repeats).
No script makes either yet.

---

## 5. Sample lists

**Purpose.** Say which captures exist and what each one is (concentration,
holder, pre-image or not), so that analyses can group them.

| File / function | Status | What it does | Output, and where it goes |
|---|---|---|---|
| `samples` table in `src/colorimeter.sqlite` | **Current** | The instrument's record of each sample, including `manual_read` (arsenic concentration) and `cap_style` (holder). `analysis_1` reads it (`_manual_read`). | — |
| `samples.py` | **Engine, frozen** | Hand-written inventory for the August pipeline. It stops at C1014. `padrect.py` still imports its `NAMES` for the self-test. Newer captures are not in it. | Self-test report on the terminal. |

---

## 6. Other files in these folders

Not scripts, but part of the clutter this review was asked to sort.

| File | Status | What it is |
|---|---|---|
| `desktop/padanalysis/README.md`, `HOWTO.md` | Current, partly out of date | Module reference and how-to. §1–7 describe the August pipeline; §8 describes `analysis_1`. |
| `desktop/padanalysis/archive/REGISTRATION.md` | Deprecated (archived) | Summary written ahead of need (see `How_best_to_help_Bill.md` §6.1). |
| `desktop/padanalysis/First Analysis - 2 - Fifteen Strips.html` and `_files/` | Record | A saved web page of the 2026-08-13 write-up. The source is `docs/pad_analysis_2026-08-13.html`. |
| `desktop/padanalysis/fig/` | — | Where almost every current desktop figure lands. Partly committed. |
| `desktop/padanalysis/archive/` | — | The August pipeline, its record files and its figures (`archive/fig/`), moved here 2026-11-11. See the chain document. |
| `devdesk/archive/` | — | `run_bound39a.py`, the superseded flat-field run. |
| `desktop/padanalysis/analysis/samples/<samp_no>.h5` | — | The interim store written by `analysis_1` (see the chain document). |
| `desktop/prompting-analysis-steps.txt`, `duotone_features_*.md` | Record | Notes and prompts from September. |

---

## How to keep this document current

When a script or function is added, retired or changes what it writes:

1. Add or edit its row, with its status and its output location.
2. If something moves to **Superseded**, name what replaced it.
3. Change the "Last reviewed" date at the top.
4. If the change affects the chain, update
   [analysis_chain_tools.md](analysis_chain_tools.md) as well.
