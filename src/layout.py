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

# ── body: reagent weight grid  y 154..214 ──────────────────────────────────────
# Two fixed rows — reagent 1 above, reagent 2 below — of four cells each, under
# a thin column-heading strip.  Positional and unchanging: a cell means the same
# thing on every screen that shows the grid and in every run state, which is the
# whole point of the grid replacing the old flip-flopping Initial/Final column.
#
#        Initial   Final    Over   Target
#     R1 [ 3.762 ][ 1.201 ][+0.020][1.450]
#     R2 [       ][       ][      ][0.650]
#
# Shown on the pre-image and both reagent phases, and on the read phase only
# while a finished capture waits for the final packet weights.
WT_HDR_INIT  = Rect( 30, 154,  70, 12)
WT_HDR_FINAL = Rect(102, 154,  70, 12)
WT_HDR_OVER  = Rect(174, 154,  70, 12)
WT_HDR_TARG  = Rect(246, 154,  70, 12)
WT1_ROW_LBL  = Rect(  6, 167,  22, 23)   # "R1"
WT1_INIT     = Rect( 30, 167,  70, 23)   # wt1_init_fld   (editable)
WT1_FINAL    = Rect(102, 167,  70, 23)   # wt1_final_fld  (editable)
WT1_OVER     = Rect(174, 167,  70, 23)   # wt1_over_lbl   (calculated)
WT1_TARG     = Rect(246, 167,  70, 23)   # wt1_targ_lbl   (calculated)
WT2_ROW_LBL  = Rect(  6, 191,  22, 23)   # row 2: same x/w as row 1, y + 24
WT2_INIT     = Rect( 30, 191,  70, 23)
WT2_FINAL    = Rect(102, 191,  70, 23)
WT2_OVER     = Rect(174, 191,  70, 23)
WT2_TARG     = Rect(246, 191,  70, 23)

# ── body: step-status chips  y 95..229 ─────────────────────────────────────────
# A "chip" is a small pill/badge widget (the UI sense of the word) — here three
# compact step-progress indicators (reagent 1, reagent 2, Capture) stacked down
# the left during the reagent/capture phases, colour-coded by the run's progress.
CHIP_A      = Rect(  90, 100,  44, 22)   # reagent 1
CHIP_B      = Rect( 136, 100,  44, 22)   # reagent 2
CHIP_C      = Rect( 182, 100,  48, 22)   # Capture

# ── body: instruction line  y 125..152 ─────────────────────────────────────────
# One line, full width.  It used to be three of 224 px, stacked down to y213:
# the narrow column on the right held the old weight entry, and the lower two
# lines formatted weights into prose.  The grid at y154 shows those as numbers
# and takes the space, leaving room for this one line above it.
INSTR1      = Rect(  8, 125, 304, 28)

# ── body: persistent collect stamp  y 188..210 ─────────────────────────────────
# Shown on the read/analysis phases (below the instructions, above the footer)
# so the collect time stays in view once stamped.  The entry phase uses the
# COLLECT form row above instead, and the reagent phases show the weight grid
# in this space.  Narrowed to match the old instruction lines.
COLLECT_LINE = Rect(  8, 188, 224, 22)

# ── body: wide label/field pairs (share y with USER and TEST rows) ─────────────
COLOR_LBL   = Rect(  8,  95, 110, 35);  COLOR_FLD  = Rect(120,  93, 130, 39)
RESULT_LBL  = Rect(  8, 135,  68, 35);  RESULT_FLD = Rect( 82, 133, 230, 39)

# ── body: read-strip prompt (ReadPhase) ────────────────────────────────────────
# Two stacked rows, same y128..186 footprint as before — bounded above by
# CHIP_C's bottom (y122) and below by COLLECT_LINE's top (y188), so neither
# overlaps:
#   row 1 (y128..156): "Manually read color:" prompt + the reading entry,
#                       side by side;
#   row 2 (y158..186): the full-width capture instruction line.
MANUAL_LBL  = Rect(  8, 128, 176, 28)   # "Manually read color:" prompt
READ_FLD    = Rect(190, 128, 122, 28)   # manual colour reading entry
READ_LBL    = Rect(  8, 158, 304, 28)   # capture instruction (single line)

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
#       Capture  | View Image
#       Defaults | Config
#       Reagent
#       Phases   | Power
MENU_CANCEL_BTN = Rect(  0,   0, 100,  50)   # top-left: discard staged choices
MENU_TITLE      = Rect(100,   5, 120,  40)   # borrowed by the config/phases/capture titles
# The menu's own title carries the version on a second line, so it needs room
# for a 44px two-line block; 6px taller than MENU_TITLE, still clear of the
# option grid at y=54.  Same background as the screen, so the extra height
# does not show.
MENU_VER_TITLE  = Rect(100,   5, 120,  46)
MENU_OK_BTN     = Rect(220,   0, 100,  50)   # top-right: save staged choices
MENU_CAPTURE    = Rect(  8,  54, 148,  56)   # row 1 left: capture choice screen
MENU_STUB1      = Rect(164,  54, 148,  56)   # row 1 right: image viewer
MENU_DEFAULTS   = Rect(  8, 118, 148,  56)   # row 2 left
MENU_STUB2      = Rect(164, 118, 148,  56)   # row 2 right: config editor
MENU_CONFIG     = Rect(  8, 182, 148,  56)   # row 3 left: reagent durations
MENU_POWER      = Rect(164, 182, 148,  56)   # row 3 right

# ── config editor (Menu → Config: every setting in the config table) ───────────
# Four body-row slots on a 38 px pitch (y 52..202), then a five-button footer
# band. The roles list and the role/browse views are never shown together, and
# use that space differently:
#
#   Roles / Restore  ┌── CFGE_ROW (left) ──┐  ┌── CFGE_ROW (right) ──┐
#   Role view        ┌── CFGE_LBL (name) ───────┐  ┌─── CFGE_BTN ────┐
#                    └── CFGE_FLD (value) ──────┘  └── Browse / Use ─┘
#
# Roles: 8 buttons (7 roles + Restore) in a 4-row x 2-column grid, width
# matching the Menu screen's grid buttons (148 px) so the two screens read as
# one family. Row-major fill: ROW1/ROW2 are row 1 left/right, and so on.
CFGE_ROW1   = Rect(  8,  52, 148, 36)
CFGE_ROW2   = Rect(164,  52, 148, 36)
CFGE_ROW3   = Rect(  8,  90, 148, 36)
CFGE_ROW4   = Rect(164,  90, 148, 36)
CFGE_ROW5   = Rect(  8, 128, 148, 36)
CFGE_ROW6   = Rect(164, 128, 148, 36)
CFGE_ROW7   = Rect(  8, 166, 148, 36)
CFGE_ROW8   = Rect(164, 166, 148, 36)
# Role view / Browse view: one row's label/value/action, four to a page. The
# name gets its own line above the value so a long value — a vendor name, the
# LED colour order — is readable at full width instead of clipped into a
# column beside its label.
CFGE_LBL1   = Rect(  6,  52, 246, 14)
CFGE_FLD1   = Rect(  4,  66, 248, 22)
CFGE_BTN1   = Rect(256,  52,  60, 36)
CFGE_LBL2   = Rect(  6,  90, 246, 14)
CFGE_FLD2   = Rect(  4, 104, 248, 22)
CFGE_BTN2   = Rect(256,  90,  60, 36)
CFGE_LBL3   = Rect(  6, 128, 246, 14)
CFGE_FLD3   = Rect(  4, 142, 248, 22)
CFGE_BTN3   = Rect(256, 128,  60, 36)
CFGE_LBL4   = Rect(  6, 166, 246, 14)
CFGE_FLD4   = Rect(  4, 180, 248, 22)
CFGE_BTN4   = Rect(256, 166,  60, 36)
# Its own footer band, taller than the standard one: five labelled actions do
# not fit in 21 px.  Shown instead of PRIOR/CLOCK/MENU/NEXT, never with them.
CFGE_PRIOR    = Rect(  0, 206,  62, 33)
CFGE_ROLLBACK = Rect( 64, 206,  62, 33)
CFGE_SAVE     = Rect(128, 206,  62, 33)
CFGE_DEFAULT  = Rect(192, 206,  62, 33)
CFGE_NEXT     = Rect(256, 206,  62, 33)

# ── LED Color picker (Menu → LED Colors: edit capture.led_color_order) ─────────
# A 12-slot grid pitch (24 px box, 2 px gap) shared by both rows so the active
# and inactive rows read as one family: row ACT holds the active sweep in
# capture order (up to 10 LEDs, plus two fixed trailing markers — 'dark' and
# 'jpg' — that are informational only and never move into the inactive row or
# the saved value); row INA holds all 10 LEDs in fixed wavelength-order slots,
# regardless of which are active. Between them, the four move/reorder arrows,
# the same glyphs as the image viewer's pan pad. Below row INA, the rowid and
# text of the order currently on screen, two lines so a ten-color list is
# never clipped.
LEDPICK_ACT1  = Rect(  4,  52, 24, 30)
LEDPICK_ACT2  = Rect( 30,  52, 24, 30)
LEDPICK_ACT3  = Rect( 56,  52, 24, 30)
LEDPICK_ACT4  = Rect( 82,  52, 24, 30)
LEDPICK_ACT5  = Rect(108,  52, 24, 30)
LEDPICK_ACT6  = Rect(134,  52, 24, 30)
LEDPICK_ACT7  = Rect(160,  52, 24, 30)
LEDPICK_ACT8  = Rect(186,  52, 24, 30)
LEDPICK_ACT9  = Rect(212,  52, 24, 30)
LEDPICK_ACT10 = Rect(238,  52, 24, 30)
LEDPICK_ACT11 = Rect(264,  52, 24, 30)   # 'dark' marker, fixed at sequence end
LEDPICK_ACT12 = Rect(290,  52, 24, 30)   # 'jpg' marker, fixed at sequence end
LEDPICK_ARR_LEFT  = Rect( 10,  86,  60, 36)   # ◀ earlier in sequence
LEDPICK_ARR_UP    = Rect( 90,  86,  60, 36)   # ▲ add selected inactive color
LEDPICK_ARR_DOWN  = Rect(170,  86,  60, 36)   # ▼ remove selected active color
LEDPICK_ARR_RIGHT = Rect(250,  86,  60, 36)   # ▶ later in sequence
LEDPICK_INA1  = Rect(  4, 126, 24, 30)
LEDPICK_INA2  = Rect( 30, 126, 24, 30)
LEDPICK_INA3  = Rect( 56, 126, 24, 30)
LEDPICK_INA4  = Rect( 82, 126, 24, 30)
LEDPICK_INA5  = Rect(108, 126, 24, 30)
LEDPICK_INA6  = Rect(134, 126, 24, 30)
LEDPICK_INA7  = Rect(160, 126, 24, 30)
LEDPICK_INA8  = Rect(186, 126, 24, 30)
LEDPICK_INA9  = Rect(212, 126, 24, 30)
LEDPICK_INA10 = Rect(238, 126, 24, 30)
LEDPICK_ROWID  = Rect(  8, 160, 304, 14)
LEDPICK_VALUE1 = Rect(  8, 176, 304, 14)
LEDPICK_VALUE2 = Rect(  8, 192, 304, 14)

# ── phases screen (Menu → Phases: the reagent countdown durations) ─────────────
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
# Choice form (below the CANCEL / title / SAVE row, which reuses the menu's
# corners): the LED bar, the four window settings, a hint line that also shows
# the help for a tapped label, and the action row.
CAP_LED1      = Rect(  4,  54, 28,  30)     # 10 LED swatches, pitch 31
CAP_LED_PITCH = 31
CAP_LAYER1      = Rect(  4,  86, 30, 16)    # R, Gr, Gb, B swatches, pitch 18
CAP_LAYER_PITCH = 18
CAP_LBL_HIGH_DN = Rect( 40,  88, 62, 30)    # "High DN"
CAP_FLD_HIGH_DN = Rect(104,  88, 50, 30)
CAP_LBL_LOW_DN  = Rect( 40, 122, 62, 30)    # "Low DN"
CAP_FLD_LOW_DN  = Rect(104, 122, 50, 30)
CAP_LBL_HIGH_PC = Rect(164,  88, 72, 30)    # "High %"
CAP_FLD_HIGH_PC = Rect(238,  88, 56, 30)
CAP_LBL_LOW_PC  = Rect(164, 122, 72, 30)    # "Low %"
CAP_FLD_LOW_PC  = Rect(238, 122, 56, 30)
CAP_HINT      = Rect(  6, 158, 308,  18)
CAP_SUITE     = Rect(  4, 180,  76,  46)    # Calibrate suite
CAP_COLOR     = Rect( 82, 180,  76,  46)    # Calibrate color
CAP_SHOW      = Rect(160, 180,  76,  46)    # Show
CAP_PAD       = Rect(238, 180,  78,  46)    # Pad areas: place the test pad's patches
# Show screen (Capture page's Show button), under the BACK / title / Capture row
# that reuses the menu's corners.
SHOW_MODE_EXP   = Rect(  4,  54, 154, 30)     # "One exposure"
SHOW_MODE_LAYER = Rect(162,  54, 154, 30)     # "One layer"
SHOW_PREV       = Rect(  4,  88,  40, 30)     # step to the previous exposure
SHOW_VALUE      = Rect( 48,  88, 224, 30)     # "5000 us  (3 of 7)"
SHOW_NEXT       = Rect(276,  88,  40, 30)
SHOW_LAYER1     = Rect(  4,  88,  75, 30)     # R, Gr, Gb, B, pitch 79
SHOW_LAYER_PITCH = 79
SHOW_HINT       = Rect(  6, 120, 308, 16)
SHOW_CANVAS     = Rect( 10, 138, 300, 100)    # the sorted-signal plot
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
