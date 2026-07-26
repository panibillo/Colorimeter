"""
layout.py  —  Pixel geometry for the 320x240 ILI9341 display.
-------------------------------------------------------------
Named Rects, one per widget slot, reusable as arguments to widget
constructors in pool.py.  Short names double as the searchable ids used when
debugging mock layouts (grep `id_fld`, `run_btn`, `timer`).

This is the ONE place pixels live.  Phases never mention a coordinate — they
speak in these names.  A second frontend (Flask) replaces this file and the
pool builder, and keeps everything else.

See: 
  pool.py
    widgets are assigned Rect's, colors, font size, alignment, initial text
               
    phases.py    

Screen is 320 wide x 240 tall, split into three zones:
    header  y   0..49
    body    y  50..214
    footer  y 215..239
"""

from __future__ import annotations

from geometry import Rect

SCREEN_W, SCREEN_H = 320, 240
SCREEN_BG = "white"          # cleared to this on every phase change

# ── header  y 0..49 ────────────────────────────────────────────────────────────
TITLE       = Rect(  8,   5, 183, 40)   # "New Test" -> sample id
MAIN_BTN    = Rect(193,   0,  74, 50)   # Begin / Cancel / Next
SLOT_BTN    = Rect(270,   0,  50, 50)   # A/B toggle

# ── body: 4 generic label/field rows  y 53..212 ─────────────────────────────────
# Reused across screens (entry: id/user/test/test-lot; entry2: temp/weight/
# collect) — the rects are positional, not tied to any one field's meaning.
BODY_LBL1   = Rect(  8,  55,  68, 35); BODY_FLD1 = Rect( 82,  53, 230, 39); BODY_FLD1s = Rect( 82,  53, 130, 39)
BODY_LBL2   = Rect(  8,  95,  68, 35); BODY_FLD2 = Rect( 82,  93, 230, 39); BODY_FLD2s = Rect( 82,  93, 130, 39)
BODY_LBL3   = Rect(  8, 135,  68, 35); BODY_FLD3 = Rect( 82, 133, 230, 39); BODY_FLD3s = Rect( 82, 133, 130, 39)
BODY_LBL4   = Rect(  8, 175,  68, 35); BODY_FLD4 = Rect( 82, 173, 230, 39); BODY_FLD4s = Rect( 82, 173, 130, 39)
DIV1        = Rect(  0,  92, 320,  1)   # row dividers
DIV2        = Rect(  0, 132, 320,  1)
DIV3        = Rect(  0, 172, 320,  1)

# ── body: timer cluster  y 55..117 ─────────────────────────────────────────────
TIMER_LBL   = Rect(  8,  55,  68, 67)   # Remaining / Elapsed / Final
TIMER       = Rect( 90,  55, 140, 42)   # compound: ‖ | MM:SS | ›
RUN_BTN     = Rect(240,  55,  72, 67)   # START / RESET / CAPTURE / POST

# ── body: step-status chips  y 95..229 ─────────────────────────────────────────
# A "chip" is a small pill/badge widget (the UI sense of the word) — here three
# compact step-progress indicators (reagent 1, reagent 2, Capture) stacked down
# the left during the reagent/capture phases, colour-coded by the run's progress.
CHIP_A      = Rect(  90, 100,  44, 22)   # reagent 1
CHIP_B      = Rect( 136, 100,  44, 22)   # reagent 2
CHIP_C      = Rect( 182, 100,  48, 22)   # Capture

# ── body: instruction lines  y 135..203 (aligned to label/field rows 3 & 4) ─────
INSTR1      = Rect(  8, 125, 304, 28)
INSTR2      = Rect(  8, 155, 304, 28)
INSTR3      = Rect(  8, 185, 304, 28)

# ── body: persistent collect stamp  y 188..210 ─────────────────────────────────
# Shown on the reagent/read/analysis phases (below the instructions, above the
# footer) so the collect time stays in view once stamped.  The entry phase uses
# the COLLECT form row above instead.
COLLECT_LINE = Rect(  8, 188, 304, 22)

# ── body: wide label/field pairs (share y with USER and TEST rows) ─────────────
COLOR_LBL   = Rect(  8,  95, 110, 35);  COLOR_FLD  = Rect(120,  93, 130, 39)
RESULT_LBL  = Rect(  8, 135,  68, 35);  RESULT_FLD = Rect( 82, 133, 230, 39)

# ── modal overlay ──────────────────────────────────────────────────────────────
MODAL_PANEL = Rect( 20,  50, 280, 140)
MODAL_MSG   = Rect( 32,  62, 256,  56)
MODAL_YES   = Rect( 40, 130, 100,  44)   # two-button layout (yes/no)
MODAL_NO    = Rect(180, 130, 100,  44)
MODAL_ONE   = Rect(110, 132, 100,  46)   # single-button layout (centred)
MODAL_3A    = Rect( 28, 134,  84,  46)   # three-button layout (keep/delete/resume)
MODAL_3B    = Rect(118, 134,  84,  46)
MODAL_3C    = Rect(208, 134,  84,  46)

# ── menu screen (reached by the dedicated footer MENU button — a screen of its
# own, not part of the A/B toggle) ───────────────────────────────────────────────
MENU_CANCEL_BTN = Rect(  0,   0, 100,  50)   # top-left: discard staged choices
MENU_TITLE      = Rect(100,   5, 120,  40)
MENU_OK_BTN     = Rect(220,   0, 100,  50)   # top-right: save staged choices
MENU_CAPTURE    = Rect( 60,  66, 200,  46)   # open the capture choice screen
MENU_DEFAULTS   = Rect( 60, 122, 200,  46)
MENU_POWER      = Rect( 60, 178, 200,  46)

# ── capture screen (Menu → Capture: pick path + images, run, show result) ───────
# One screen of its own, reused for three views: the choice form, the running
# progress, and the done/result. Selector rects and the wide progress rect
# overlap in y, but the two are never shown together.
CAP_TITLE     = Rect(  8,   6, 304,  34)
CAP_MODE_LBL  = Rect(  8,  52,  96,  36)     # "Path"
CAP_MODE_CAL  = Rect(106,  50, 102,  40)     # Calibrate  ┐ mutually-exclusive
CAP_MODE_NORM = Rect(212,  50, 100,  40)     # Normal     ┘ (selected = green)
CAP_N_LBL     = Rect(  8, 100,  96,  36)     # "Images"
CAP_N1        = Rect(106,  98,  38,  40)     # 1..5 images per LED
CAP_N2        = Rect(148,  98,  38,  40)
CAP_N3        = Rect(190,  98,  38,  40)
CAP_N4        = Rect(232,  98,  38,  40)
CAP_N5        = Rect(274,  98,  38,  40)
CAP_PROGRESS  = Rect(  8,  56, 304, 120)     # progress / result text
CAP_BACK      = Rect(  8, 184, 100,  48)     # Back / Cancel
CAP_START     = Rect(160, 184, 152,  48)     # START / OK

# ── footer  y 215..239 ─────────────────────────────────────────────────────────
PRIOR_BTN   = Rect(  0, 217,  58, 21)
CLOCK       = Rect( 60, 217, 140, 21)   # "Jul 21 14:32:07"
MENU_BTN    = Rect(202, 217,  58, 21)
NEXT_BTN    = Rect(262, 217,  58, 21)
