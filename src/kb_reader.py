"""
kb_reader.py  —  Non-blocking USB keyboard reader via evdev.
------------------------------------------------------------
Reads from /dev/input/eventX directly, so it works in all three deployment
scenarios:

  1. SSH + physical USB keyboard on the Pi  (stdin is the remote terminal,
     not the local keyboard — evdev bypasses that entirely)
  2. systemd autostart  (no TTY; stdin is /dev/null or a journal socket)
  3. Web-edited fields  (keyboard reader is simply idle; the field is updated
     by set_value() from the network layer)

Prerequisites
-------------
  pip install evdev
  sudo usermod -aG input <username>   # give the process user /dev/input access
  # (or run as root — not recommended for production)

Usage
-----
    kb = KbReader()
    if not kb.open():
        log.warning("No keyboard found")

    # in main loop:
    key = kb.read_one()    # returns a key string or None
    if key:
        mgr.on_key(key)

    # at exit:
    kb.cleanup()

Key strings returned
--------------------
  Printable characters  : 'a', 'A', '1', '!', ' ', etc.
  Navigation            : 'tab', 'shift-tab', 'enter', 'backspace',
                          'delete', 'escape', 'up', 'down', 'left', 'right'
  Modifier-only keys    : None  (shift state is tracked internally)

Shift is tracked (KEY_LEFTSHIFT / KEY_RIGHTSHIFT).
Caps Lock is not implemented.
Auto-repeat fires only for 'backspace' (value=2 events).
"""

import logging
import select
from typing import Optional

log = logging.getLogger(__name__)

try:
    import evdev
    from evdev import ecodes
    _EVDEV_AVAILABLE = True
except ImportError:
    _EVDEV_AVAILABLE = False
    log.warning("kb_reader: evdev not installed — keyboard input unavailable")


# ── Key maps ──────────────────────────────────────────────────────────────────

def _build_maps():
    """Build char and nav maps from evdev constants (called only if evdev present)."""
    c = ecodes

    char_map = {
        c.KEY_A: ('a','A'), c.KEY_B: ('b','B'), c.KEY_C: ('c','C'),
        c.KEY_D: ('d','D'), c.KEY_E: ('e','E'), c.KEY_F: ('f','F'),
        c.KEY_G: ('g','G'), c.KEY_H: ('h','H'), c.KEY_I: ('i','I'),
        c.KEY_J: ('j','J'), c.KEY_K: ('k','K'), c.KEY_L: ('l','L'),
        c.KEY_M: ('m','M'), c.KEY_N: ('n','N'), c.KEY_O: ('o','O'),
        c.KEY_P: ('p','P'), c.KEY_Q: ('q','Q'), c.KEY_R: ('r','R'),
        c.KEY_S: ('s','S'), c.KEY_T: ('t','T'), c.KEY_U: ('u','U'),
        c.KEY_V: ('v','V'), c.KEY_W: ('w','W'), c.KEY_X: ('x','X'),
        c.KEY_Y: ('y','Y'), c.KEY_Z: ('z','Z'),

        c.KEY_1: ('1','!'), c.KEY_2: ('2','@'), c.KEY_3: ('3','#'),
        c.KEY_4: ('4','$'), c.KEY_5: ('5','%'), c.KEY_6: ('6','^'),
        c.KEY_7: ('7','&'), c.KEY_8: ('8','*'), c.KEY_9: ('9','('),
        c.KEY_0: ('0',')'),

        c.KEY_SPACE:      (' ', ' '),
        c.KEY_MINUS:      ('-', '_'),
        c.KEY_EQUAL:      ('=', '+'),
        c.KEY_LEFTBRACE:  ('[', '{'),
        c.KEY_RIGHTBRACE: (']', '}'),
        c.KEY_SEMICOLON:  (';', ':'),
        c.KEY_APOSTROPHE: ("'", '"'),
        c.KEY_GRAVE:      ('`', '~'),
        c.KEY_BACKSLASH:  ('\\','|'),
        c.KEY_COMMA:      (',', '<'),
        c.KEY_DOT:        ('.', '>'),
        c.KEY_SLASH:      ('/', '?'),
    }

    nav_map = {
        c.KEY_ENTER:     'enter',
        c.KEY_KPENTER:   'enter',
        c.KEY_TAB:       'tab',
        c.KEY_BACKSPACE: 'backspace',
        c.KEY_DELETE:    'delete',
        c.KEY_ESC:       'escape',
        c.KEY_UP:        'up',
        c.KEY_DOWN:      'down',
        c.KEY_LEFT:      'left',
        c.KEY_RIGHT:     'right',
    }

    shift_keys = {c.KEY_LEFTSHIFT, c.KEY_RIGHTSHIFT}

    return char_map, nav_map, shift_keys


if _EVDEV_AVAILABLE:
    _CHAR_MAP, _NAV_MAP, _SHIFT_KEYS = _build_maps()
else:
    _CHAR_MAP = _NAV_MAP = _SHIFT_KEYS = {}


# ── KbReader class ────────────────────────────────────────────────────────────

class KbReader:
    """
    Non-blocking keyboard reader.  One instance per application.

    open() must be called before read_one().  If no keyboard is present,
    open() returns False and read_one() always returns None — the rest of
    the application is unaffected.
    """

    def __init__(self) -> None:
        self._dev   = None
        self._shift = False

    def open(self) -> bool:
        """
        Find and open the first USB keyboard device.

        Selects the first /dev/input device that has EV_KEY capability and
        includes KEY_A (filters out mice, joysticks, and other HID devices).
        Returns True if a keyboard was found and opened.
        """
        if not _EVDEV_AVAILABLE:
            return False

        try:
            paths = evdev.list_devices()
        except Exception as exc:
            log.warning("KbReader.open: list_devices failed: %s", exc)
            return False

        log.info("KbReader.open: scanning %d device(s)", len(paths))
        for path in paths:
            try:
                dev = evdev.InputDevice(path)
                caps = dev.capabilities()
                has_ev_key = ecodes.EV_KEY in caps
                has_key_a  = has_ev_key and ecodes.KEY_A in caps[ecodes.EV_KEY]
                log.info(
                    "KbReader:  %s  name=%r  EV_KEY=%s  KEY_A=%s",
                    path, dev.name, has_ev_key, has_key_a,
                )
                if has_ev_key and has_key_a:
                    self._dev = dev
                    log.info("KbReader: selected %s (%s)", dev.name, dev.path)
                    return True
                dev.close()
            except Exception as exc:
                log.info("KbReader:  %s  open failed: %s", path, exc)

        log.warning("KbReader.open: no keyboard device found — check: "
                    "'groups' includes 'input', 'ls /dev/input/', evdev installed")
        return False

    def read_one(self) -> Optional[str]:
        """
        Return the next key string, or None if nothing is pending.

        Never blocks.  Call in the main event loop on every iteration.
        Handles disconnect gracefully (returns None after logging a warning).
        """
        #log.info('read_one')
        if self._dev is None:
            return None

        # Non-blocking check — avoids blocking when no key is pending.
        try:
            ready, _, _ = select.select([self._dev.fd], [], [], 0)
        except Exception:
            return None

        if not ready:
            return None

        try:
            events = self._dev.read()
        except OSError as exc:
            log.warning("KbReader: read error (%s) — keyboard disconnected?", exc)
            self._dev = None
            return None

        for event in events:
            if event.type != ecodes.EV_KEY:
                continue
            key_str = self._translate(event.code, event.value)
            if key_str is not None:
                return key_str   # return first translatable event; rest queue next call

        return None

    def _translate(self, code: int, value: int) -> Optional[str]:
        """
        Translate an EV_KEY event (code + value) to a key string.

        value: 1 = key-down, 2 = auto-repeat, 0 = key-up.
        Only key-down and (for backspace) auto-repeat events produce output.
        """
        # Track shift state on both down and up.
        if code in _SHIFT_KEYS:
            self._shift = (value != 0)   # 0 = key-up
            return None

        if value == 0:       # key-up — no output
            return None

        # Auto-repeat: only pass backspace through (natural typing feel).
        if value == 2 and code != ecodes.KEY_BACKSPACE:
            return None

        # Navigation keys.
        if code in _NAV_MAP:
            nav = _NAV_MAP[code]
            if nav == 'tab' and self._shift:
                return 'shift-tab'
            return nav

        # Printable characters.
        if code in _CHAR_MAP:
            pair = _CHAR_MAP[code]
            return pair[1] if self._shift else pair[0]

        return None

    def cleanup(self) -> None:
        """Close the keyboard device."""
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None
            log.info("KbReader: closed")
