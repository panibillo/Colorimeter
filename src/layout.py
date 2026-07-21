"""
layout.py  —  Pixel geometry for the 320x240 ILI9341 display.
-------------------------------------------------------------
Named Rects, one per widget slot, reusable as arguments to widget
constructors in pool.py.  Short names double as the searchable ids used when
debugging mock layouts (grep `id_fld`, `run_btn`, `timer`).

This is the ONE place pixels live.  Phases never mention a coordinate — they
speak in these names.  A second frontend (Flask) replaces this file and the
pool builder, and keeps everything else.

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

# ── body: sample-entry form  y 53..212 ─────────────────────────────────────────
ID_LBL      = Rect(  8,  55,  68, 35);  ID_FLD      = Rect( 82,  53, 230, 39)
USER_LBL    = Rect(  8,  95,  68, 35);  USER_FLD    = Rect( 82,  93, 230, 39)
TEST_LBL    = Rect(  8, 135,  68, 35);  TEST_FLD    = Rect( 82, 133, 230, 39)
COLLECT_LBL = Rect(  8, 175,  68, 35);  COLLECT_FLD = Rect( 82, 173, 230, 39)
DIV1        = Rect(  0,  92, 320,  1)   # row dividers
DIV2        = Rect(  0, 132, 320,  1)
DIV3        = Rect(  0, 172, 320,  1)

# ── body: timer cluster  y 60..102 ─────────────────────────────────────────────
CAPTION     = Rect(  8,  64,  68, 35)   # Remaining / Elapsed / Final
TIMER       = Rect( 90,  60, 140, 42)   # compound: ‖ | MM:SS | ›
RUN_BTN     = Rect(240,  64,  72, 36)   # START / RESET / CAPTURE / POST

# ── body: instruction lines  y 125..185 ────────────────────────────────────────
INSTR1      = Rect( 14, 125, 280, 28)
INSTR2      = Rect( 14, 157, 280, 28)

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
MODAL_YES   = Rect( 40, 130, 100,  44)
MODAL_NO    = Rect(180, 130, 100,  44)

# ── footer  y 215..239 ─────────────────────────────────────────────────────────
PRIOR_BTN   = Rect(  0, 217,  78, 21)
CLOCK       = Rect( 80, 217, 120, 21)
NEXT_BTN    = Rect(260, 217,  78, 21)
