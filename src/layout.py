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
# Visible alarm: shown left of MAIN_BTN over the right half of the title when a
# backgrounded slot's timer expired unattended ("Resume\n<slot>"). Overlaps the
# title's (usually empty) right half — short sample ids are not obscured.
ALARM_BTN   = Rect( 98,   0,  93, 50)

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

# ── body: read-strip prompt (ReadPhase) ────────────────────────────────────────
# A wide multi-line label carries the capture instruction AND the "Read color"
# prompt (black on white); the manual-reading entry sits to its right with its
# left margin moved over to make room. Ends above COLLECT_LINE (y188) so the two
# no longer overlap.
READ_LBL    = Rect(  8, 128, 176, 58)   # instruction + "Read color:" (multi-line)
READ_FLD    = Rect(190, 138, 122, 40)   # manual colour reading entry

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
# Options sit in a 3-row x 2-column grid filling the body; the menu draws no
# footer, so the bottom row may run to the foot of the screen.
#
#       Capture  | stub1
#       Defaults | stub2
#       Config   | Power
MENU_CANCEL_BTN = Rect(  0,   0, 100,  50)   # top-left: discard staged choices
MENU_TITLE      = Rect(100,   5, 120,  40)
MENU_OK_BTN     = Rect(220,   0, 100,  50)   # top-right: save staged choices
MENU_CAPTURE    = Rect(  8,  54, 148,  56)   # row 1 left: capture choice screen
MENU_STUB1      = Rect(164,  54, 148,  56)   # row 1 right: unassigned
MENU_DEFAULTS   = Rect(  8, 118, 148,  56)   # row 2 left
MENU_STUB2      = Rect(164, 118, 148,  56)   # row 2 right: unassigned
MENU_CONFIG     = Rect(  8, 182, 148,  56)   # row 3 left: config screen
MENU_POWER      = Rect(164, 182, 148,  56)   # row 3 right

# ── config screen (Menu → Config: the reagent countdown durations) ──────────────
# Two identical rows — a label, the value in seconds, and four step buttons
# (-60 / -10 / +10 / +60).  The value is an editable field as well, so a
# keyboard can type a duration straight in.  CANCEL/OK reuse the menu's corners.
CFG_LBL1    = Rect(  6,  58,  78,  40); CFG_FLD1 = Rect( 88,  58,  60,  40)
CFG_R1_M60  = Rect(152,  58,  39,  40)   # −60 s  ┐
CFG_R1_M10  = Rect(193,  58,  39,  40)   # −10 s  │ reagent 1
CFG_R1_P10  = Rect(234,  58,  39,  40)   # +10 s  │
CFG_R1_P60  = Rect(275,  58,  39,  40)   # +60 s  ┘
CFG_LBL2    = Rect(  6, 104,  78,  40); CFG_FLD2 = Rect( 88, 104,  60,  40)
CFG_R2_M60  = Rect(152, 104,  39,  40)   # −60 s  ┐
CFG_R2_M10  = Rect(193, 104,  39,  40)   # −10 s  │ reagent 2
CFG_R2_P10  = Rect(234, 104,  39,  40)   # +10 s  │
CFG_R2_P60  = Rect(275, 104,  39,  40)   # +60 s  ┘
CFG_HINT    = Rect(  8, 152, 304,  26)   # units + the mm:ss the seconds mean
CFG_RESTORE = Rect(  8, 184, 148,  48)   # back to the configured durations

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
CAP_BACK      = Rect(  8, 184, 100,  48)     # Cancel (running-capture progress view)
CAP_START     = Rect( 84, 184, 152,  48)     # "Calibrate Exposures" (run) / done OK

# ── image viewer (Menu → View Image, and Read → View Sample) ────────────────────
# A 200x240 canvas fills the left of the screen — taller than wide, matching the
# sample rectangle, so the sample ROI plus a margin fills the view well. The
# right column carries a 3x3 pad of 40x40 zoom/pan buttons, a strip showing the
# selected ROI's name, and a 2x2 grid of mode buttons.
#
# The viewer draws no header and no footer: the canvas runs the full height, and
# Exit is one of the mode buttons.
#
#        0                200      240      280   320
#      0 ┌─────────────────┬────────┬────────┬────────┐
#        │                 │   +    │   ▲    │   −    │  40
#     40 │                 ├────────┼────────┼────────┤
#        │                 │   ◀    │  Home  │   ▶    │  40
#     80 │                 ├────────┼────────┼────────┤
#        │   canvas        │  1px   │   ▼    │  Img   │  40
#    120 │   200 x 240     ├────────┴────────┴────────┤
#        │                 │      < roi name >        │  26
#    146 │                 ├────────────┬─────────────┤
#        │                 │    Edit    │    Save     │  47
#    193 │                 ├────────────┼─────────────┤
#        │                 │   Cancel   │    Exit     │  47
#    240 └─────────────────┴────────────┴─────────────┘
IMG_CANVAS   = Rect(  0,   0, 200, 240)   # the image + ROI overlays
IMG_ZOOM_IN  = Rect(200,   0,  40,  40)   # +
IMG_UP       = Rect(240,   0,  40,  40)   # ▲
IMG_ZOOM_OUT = Rect(280,   0,  40,  40)   # −
IMG_LEFT     = Rect(200,  40,  40,  40)   # ◀
IMG_FIT      = Rect(240,  40,  40,  40)   # Fit — whole frame, the pad's "home"
IMG_RIGHT    = Rect(280,  40,  40,  40)   # ▶
IMG_STEP     = Rect(200,  80,  40,  40)   # edit step: 1px ⇄ 10px
IMG_DOWN     = Rect(240,  80,  40,  40)   # ▼
IMG_REIMAGE  = Rect(280,  80,  40,  40)   # Img — fresh white-LED exposure
IMG_NAME     = Rect(200, 120, 120,  26)   # selected ROI name; blank when none
IMG_EDIT     = Rect(200, 146,  60,  47)
IMG_SAVE     = Rect(260, 146,  60,  47)
IMG_CANCEL   = Rect(200, 193,  60,  47)
IMG_EXIT     = Rect(260, 193,  60,  47)

# ── recapture options dialog (viewer's Img button) ───────────────────────────
# Replaces the viewer's canvas+pad while open; reuses the Menu header chrome
# (menu_cancel_btn/menu_title/menu_ok_btn) for Cancel / title / Capture, so no
# corner buttons are defined here. Two radio pairs (Focus, tri-state: either
# button or neither; Exposure, two-state) each with an on/off save toggle in
# a fourth column, plus a third row of two non-exclusive save-image toggles
# (Bound ROI, Whole Image — either, neither, or both; no toggle column of its
# own, the buttons themselves carry the on/off state).
RECAP_FOCUS_LBL  = Rect(  8,  54,  96,  46)   # "Focus"
RECAP_FOCUS_AUTO = Rect(106,  54,  75,  46)   # "Auto Focus"
RECAP_FOCUS_CFG  = Rect(185,  54,  75,  46)   # "Reread Cfg"
RECAP_SAVE_FOCUS = Rect(270,  54,  42,  46)   # toggle
RECAP_EXP_LBL    = Rect(  8, 110,  96,  46)   # "Exposure"
RECAP_EXP_SAMPLE = Rect(106, 110,  75,  46)   # "Sample Rect"
RECAP_EXP_WHOLE  = Rect(185, 110,  75,  46)   # "Whole Image"
RECAP_SAVE_EXP   = Rect(270, 110,  42,  46)   # toggle
RECAP_IMAGE_LBL  = Rect(  8, 166,  96,  46)   # "Save Image"
RECAP_IMAGE_ROI  = Rect(106, 166,  75,  46)   # "Bound ROI"
RECAP_IMAGE_WHOLE= Rect(185, 166,  75,  46)   # "Whole Image"
RECAP_IMAGE_DARK = Rect(270, 166,  42,  46)   # "Dark"

# ── footer  y 215..239 ─────────────────────────────────────────────────────────
PRIOR_BTN   = Rect(  0, 217,  58, 21)
CLOCK       = Rect( 60, 217, 140, 21)   # "Jul 21 14:32:07"
MENU_BTN    = Rect(202, 217,  58, 21)
NEXT_BTN    = Rect(262, 217,  58, 21)
